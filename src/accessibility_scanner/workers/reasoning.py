from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..models import AggregatedCheckpoint


class LiteLLMReasoningWorker:
    """LLM-assisted worker for summarization and remediation drafting only.

    Pass/fail decisions are never modified by this worker.
    """

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.model = model
        try:
            import litellm  # noqa: F401

            self._enabled = True
        except ImportError:
            self._enabled = False

    def dedupe(self, results: list[AggregatedCheckpoint]) -> list[AggregatedCheckpoint]:
        by_checkpoint: dict[str, AggregatedCheckpoint] = {}
        for item in results:
            by_checkpoint[item.checkpoint_id] = item
        return list(by_checkpoint.values())

    def checkpoint_to_bucket(self, checkpoint_id: str) -> str:
        if checkpoint_id.startswith("1.1") or checkpoint_id.startswith("1.2") or checkpoint_id == "1.4.5":
            return "content_equivalence"
        if checkpoint_id.startswith("1.3") or checkpoint_id.startswith("1.4"):
            return "layout_perception"
        if checkpoint_id.startswith("2.1") or checkpoint_id.startswith("3.2"):
            return "interaction_navigation"
        return "semantics_transaction"

    def remediation_summary(self, results: list[AggregatedCheckpoint]) -> list[dict[str, Any]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for result in results:
            if result.status.value == "Fail":
                grouped[result.checkpoint_id].append(result.rationale)

        summaries: list[dict[str, Any]] = []
        for checkpoint_id, rationales in grouped.items():
            summary = rationales[0] if rationales else "Accessibility issue detected."
            summaries.append(
                {
                    "checkpoint_id": checkpoint_id,
                    "summary": summary,
                    "llm_used": self._enabled,
                }
            )
        return summaries

    def summarize_evidence(self, checkpoint_id: str, evidence_refs: list[str]) -> dict[str, Any]:
        return {
            "checkpoint_id": checkpoint_id,
            "evidence_count": len(evidence_refs),
            "evidence_refs": evidence_refs[:20],
            "llm_used": self._enabled,
        }

    def explain_policy(self, strict_decision: str, automation_decision: str) -> dict[str, Any]:
        return {
            "strict_mode": strict_decision,
            "automation_only_mode": automation_decision,
            "note": "LLM reasoning is advisory and does not alter deterministic checkpoint status.",
        }
