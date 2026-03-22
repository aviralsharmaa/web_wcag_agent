from __future__ import annotations

import uuid
from collections import deque
from typing import Any

from .buckets import (
    analyze_content_equivalence,
    analyze_interaction_navigation,
    analyze_layout_perception,
    analyze_semantics_transaction,
)
from .checkpoints import BUCKET_TO_CHECKPOINTS
from .crawler import CrawlQueue, RobotsGate, expand_frontier
from .evidence_store import EvidenceStore
from .models import (
    Bucket,
    CheckpointResult,
    CheckpointStatus,
    CrawlTarget,
    PolicyMode,
    ScanReport,
    ScanRequest,
)
from .policy import aggregate_checkpoint_results, compute_totals, policy_decision
from .reporting import write_report
from .state import ScanState
from .workers import DeterministicWorkerSuite, LiteLLMReasoningWorker

try:
    from langgraph.graph import END, StateGraph

    HAS_LANGGRAPH = True
except Exception:
    HAS_LANGGRAPH = False
    END = "END"
    StateGraph = None


class LangGraphScanner:
    def __init__(
        self,
        fetcher,
        evidence_store: EvidenceStore | None = None,
        deterministic_workers: DeterministicWorkerSuite | None = None,
        reasoning_worker: LiteLLMReasoningWorker | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.evidence_store = evidence_store or EvidenceStore()
        self.deterministic_workers = deterministic_workers or DeterministicWorkerSuite()
        self.reasoning_worker = reasoning_worker or LiteLLMReasoningWorker()
        self.robots_gate = RobotsGate()
        self._graph = self._build_graph() if HAS_LANGGRAPH else None

    def run(self, request: ScanRequest) -> ScanReport:
        state: ScanState = {"request": request}
        try:
            if self._graph is not None:
                final_state = self._graph.invoke(state)
            else:
                final_state = self._run_fallback(state)
        finally:
            self.fetcher.teardown()

        return final_state["policy_outputs"]["report"]

    def _build_graph(self):
        graph = StateGraph(ScanState)
        graph.add_node("init_run", self.init_run)
        graph.add_node("auth_session_setup", self.auth_session_setup)
        graph.add_node("crawl_discovery", self.crawl_discovery)
        graph.add_node("page_fetch_render", self.page_fetch_render)
        graph.add_node("bucket_router", self.bucket_router)
        graph.add_node("bucket_execution", self.bucket_execution)
        graph.add_node("aggregate_results", self.aggregate_results)
        graph.add_node("policy_decision", self.policy_decision_node)
        graph.add_node("report_emit", self.report_emit)

        graph.set_entry_point("init_run")
        graph.add_edge("init_run", "auth_session_setup")
        graph.add_edge("auth_session_setup", "crawl_discovery")
        graph.add_conditional_edges(
            "crawl_discovery",
            self._route_after_crawl,
            {
                "fetch": "page_fetch_render",
                "done": "aggregate_results",
            },
        )
        graph.add_edge("page_fetch_render", "bucket_router")
        graph.add_edge("bucket_router", "bucket_execution")
        graph.add_edge("bucket_execution", "crawl_discovery")
        graph.add_edge("aggregate_results", "policy_decision")
        graph.add_edge("policy_decision", "report_emit")
        graph.add_edge("report_emit", END)
        return graph.compile()

    def _run_fallback(self, state: ScanState) -> ScanState:
        state.update(self.init_run(state))
        state.update(self.auth_session_setup(state))
        while True:
            state.update(self.crawl_discovery(state))
            if state.get("crawl_complete"):
                break
            state.update(self.page_fetch_render(state))
            state.update(self.bucket_router(state))
            state.update(self.bucket_execution(state))
        state.update(self.aggregate_results(state))
        state.update(self.policy_decision_node(state))
        state.update(self.report_emit(state))
        return state

    def _route_after_crawl(self, state: ScanState) -> str:
        return "done" if state.get("crawl_complete") else "fetch"

    def init_run(self, state: ScanState) -> dict[str, Any]:
        request = state["request"]
        run_id = str(uuid.uuid4())
        run_dir = str(self.evidence_store.run_dir(run_id))
        crawl_queue = CrawlQueue.from_start_urls(request)
        return {
            "run_id": run_id,
            "artifacts_dir": run_dir,
            "frontier": list(crawl_queue.queue),
            "visited": set(),
            "current_target": None,
            "crawl_complete": False,
            "page_artifacts": {},
            "per_page_results": [],
            "aggregate_results": [],
            "evidence_index": {},
            "policy_outputs": {},
            "errors": [],
            "auth_context": None,
            "active_buckets": [],
            "report_path": None,
        }

    def auth_session_setup(self, state: ScanState) -> dict[str, Any]:
        request = state["request"]
        auth_context = self.fetcher.setup(request)
        return {"auth_context": auth_context}

    def crawl_discovery(self, state: ScanState) -> dict[str, Any]:
        request = state["request"]
        visited = state.get("visited", set())
        frontier = list(state.get("frontier", []))

        # Skip already-visited URLs if they remain queued.
        while frontier and frontier[0].url in visited:
            frontier.pop(0)

        if len(visited) >= request.max_pages or not frontier:
            return {
                "frontier": frontier,
                "current_target": None,
                "crawl_complete": True,
            }

        current = frontier.pop(0)
        return {
            "frontier": frontier,
            "current_target": current,
            "crawl_complete": False,
        }

    def page_fetch_render(self, state: ScanState) -> dict[str, Any]:
        request = state["request"]
        target = state.get("current_target")
        if target is None:
            return {}

        run_id = state["run_id"]
        artifact = self.fetcher.fetch_page(target.url, target.depth, request, run_id)

        dom_id = self.evidence_store.add_text(
            run_id,
            f"dom-{_safe_name(target.url)}",
            artifact.html,
            metadata={"url": target.url, "kind": "dom_snapshot"},
        )
        artifact.dom_evidence_id = dom_id

        screenshot_path = artifact.render_metrics.get("screenshot_path")
        if screenshot_path:
            artifact.screenshot_evidence_id = self.evidence_store.add_file_ref(
                "screenshot",
                screenshot_path,
                metadata={"url": target.url},
            )

        artifact = self.deterministic_workers.enrich_page(artifact)

        page_artifacts = dict(state.get("page_artifacts", {}))
        page_artifacts[target.url] = artifact

        visited = set(state.get("visited", set()))
        visited.add(target.url)

        queue = CrawlQueue(deque(state.get("frontier", [])))
        expand_frontier(
            request=request,
            visited=visited,
            queue=queue,
            base_url=target.url,
            links=artifact.links,
            depth=target.depth,
            robots_gate=self.robots_gate,
        )

        return {
            "page_artifacts": page_artifacts,
            "visited": visited,
            "frontier": list(queue.queue),
        }

    def bucket_router(self, state: ScanState) -> dict[str, Any]:
        request = state["request"]
        enabled_by_bucket: list[str] = []
        for bucket in Bucket:
            checkpoint_ids = BUCKET_TO_CHECKPOINTS[bucket]
            if any(request.checkpoint_overrides.get(checkpoint_id, True) for checkpoint_id in checkpoint_ids):
                enabled_by_bucket.append(bucket.value)
        return {"active_buckets": enabled_by_bucket}

    def bucket_execution(self, state: ScanState) -> dict[str, Any]:
        target = state.get("current_target")
        if target is None:
            return {}

        request = state["request"]
        page = state["page_artifacts"][target.url]

        analyzers = {
            Bucket.CONTENT_EQUIVALENCE.value: analyze_content_equivalence,
            Bucket.LAYOUT_PERCEPTION.value: analyze_layout_perception,
            Bucket.INTERACTION_NAVIGATION.value: analyze_interaction_navigation,
            Bucket.SEMANTICS_TRANSACTION.value: analyze_semantics_transaction,
        }

        results: list[CheckpointResult] = []
        for bucket_name in state.get("active_buckets", []):
            findings = analyzers[bucket_name](page)
            for finding in findings:
                if request.checkpoint_overrides.get(finding.checkpoint_id, True):
                    results.append(finding)

        per_page_results = list(state.get("per_page_results", []))
        per_page_results.extend(results)
        return {"per_page_results": per_page_results}

    def aggregate_results(self, state: ScanState) -> dict[str, Any]:
        per_page_results = list(state.get("per_page_results", []))
        nav_result = self._consistent_navigation_result(state)
        if nav_result is not None:
            per_page_results.append(nav_result)

        aggregate = aggregate_checkpoint_results(per_page_results)
        aggregate = self.reasoning_worker.dedupe(aggregate)

        totals = compute_totals(aggregate)
        cannot_verify = [item.checkpoint_id for item in aggregate if item.status == CheckpointStatus.CANNOT_VERIFY]

        strict = policy_decision(aggregate, PolicyMode.STRICT_GOV)
        automation = policy_decision(aggregate, PolicyMode.AUTOMATION_ONLY)

        remediation = self.reasoning_worker.remediation_summary(aggregate)
        evidence_summaries = [
            self.reasoning_worker.summarize_evidence(item.checkpoint_id, item.evidence_refs) for item in aggregate
        ]
        policy_note = self.reasoning_worker.explain_policy(strict, automation)

        return {
            "per_page_results": per_page_results,
            "aggregate_results": [item.to_dict() for item in aggregate],
            "policy_outputs": {
                "totals": totals,
                "cannot_verify": cannot_verify,
                "strict": strict,
                "automation": automation,
                "remediation": remediation,
                "evidence_summaries": evidence_summaries,
                "policy_note": policy_note,
                "aggregate_objects": aggregate,
            },
        }

    def policy_decision_node(self, state: ScanState) -> dict[str, Any]:
        # Decisions are produced in aggregate_results and kept deterministic.
        return {}

    def report_emit(self, state: ScanState) -> dict[str, Any]:
        run_id = state["run_id"]
        request = state["request"]
        policy_outputs = state["policy_outputs"]
        aggregate = policy_outputs["aggregate_objects"]
        per_page = state["per_page_results"]

        self.evidence_store.dump_index(run_id)

        report = ScanReport(
            run_id=run_id,
            request=request,
            totals=policy_outputs["totals"],
            checkpoint_results=aggregate,
            per_page_results=per_page,
            strict_decision=policy_outputs["strict"],
            automation_decision=policy_outputs["automation"],
            cannot_verify_checkpoints=policy_outputs["cannot_verify"],
            remediation_summary=policy_outputs["remediation"],
            artifacts_dir=state["artifacts_dir"],
            errors=list(state.get("errors", [])),
        )

        report_path = write_report(report, state["artifacts_dir"])
        policy_outputs = dict(policy_outputs)
        policy_outputs["report"] = report

        return {
            "evidence_index": self.evidence_store.index,
            "policy_outputs": policy_outputs,
            "report_path": report_path,
        }

    def _consistent_navigation_result(self, state: ScanState) -> CheckpointResult | None:
        artifacts = state.get("page_artifacts", {})
        if not artifacts:
            return None

        signatures = []
        pages = []
        for url, artifact in artifacts.items():
            sig = artifact.interaction_metrics.get("nav_order_signature")
            if sig:
                signatures.append(tuple(sig))
                pages.append(url)

        if len(signatures) < 2:
            return CheckpointResult(
                checkpoint_id="3.2.3",
                bucket=Bucket.INTERACTION_NAVIGATION,
                status=CheckpointStatus.CANNOT_VERIFY,
                applicable=True,
                page_url="__site__",
                selector_or_target="navigation",
                evidence_refs=[],
                rationale="Insufficient multi-page navigation evidence to verify consistency.",
                manual_required=True,
            )

        all_same = len(set(signatures)) == 1
        return CheckpointResult(
            checkpoint_id="3.2.3",
            bucket=Bucket.INTERACTION_NAVIGATION,
            status=CheckpointStatus.PASS if all_same else CheckpointStatus.FAIL,
            applicable=True,
            page_url="__site__",
            selector_or_target="navigation",
            evidence_refs=[],
            rationale="Navigation order compared across crawled pages.",
            manual_required=True,
        )


def _safe_name(url: str) -> str:
    return (
        url.replace("https://", "")
        .replace("http://", "")
        .replace("/", "_")
        .replace("?", "_")
        .replace("&", "_")
    )


def run_scan(
    request: ScanRequest,
    fetcher=None,
    artifacts_root: str = "artifacts",
) -> ScanReport:
    from .fetchers import PlaywrightFetcher

    resolved_fetcher = fetcher or PlaywrightFetcher(artifacts_root=artifacts_root)
    scanner = LangGraphScanner(fetcher=resolved_fetcher, evidence_store=EvidenceStore(root=artifacts_root))
    return scanner.run(request)
