"""Analyzes a single screen for WCAG issues using the existing bucket pipeline."""
from __future__ import annotations

import logging
import re
from typing import Any

from ..buckets import (
    analyze_content_equivalence,
    analyze_interaction_navigation,
    analyze_layout_perception,
    analyze_semantics_transaction,
)
from ..checkpoints import CHECKPOINTS
from ..models import CheckpointResult, CheckpointStatus, PageArtifact
from ..workers import DeterministicWorkerSuite

logger = logging.getLogger(__name__)

_CROSS_PAGE_NOT_APPLICABLE = {"3.2.4", "3.2.6", "3.3.7"}
_EXPLICIT_RISK_FAIL = {"1.4.5", "2.2.1", "2.2.2", "2.5.1", "2.5.2", "2.5.7"}
_RISK_SIGNAL_RE = re.compile(
    r"(gesture|mousedown|drag|timer|timeout|carousel|animation|text-in-image|captcha|"
    r"autoplay|live caption|audio description|cognitive|puzzle|strobe|flashes?)",
    re.IGNORECASE,
)


class ScreenAnalyzer:
    """Runs all four WCAG buckets on a single PageArtifact."""

    def __init__(
        self,
        cannot_verify_policy: str = "pass_leaning",
        cannot_verify_threshold: int = 31,
        cannot_verify_enforcement: str = "both",
    ) -> None:
        self.workers = DeterministicWorkerSuite()
        self.cannot_verify_policy = (cannot_verify_policy or "pass_leaning").strip().lower()
        try:
            self.cannot_verify_threshold = int(cannot_verify_threshold)
        except (TypeError, ValueError):
            self.cannot_verify_threshold = 31
        self.cannot_verify_enforcement = (cannot_verify_enforcement or "both").strip().lower()

    def analyze(self, artifact: PageArtifact) -> list[CheckpointResult]:
        artifact = self.workers.enrich_page(artifact)

        results: list[CheckpointResult] = []
        for analyzer in [
            analyze_content_equivalence,
            analyze_layout_perception,
            analyze_interaction_navigation,
            analyze_semantics_transaction,
        ]:
            try:
                results.extend(analyzer(artifact))
            except Exception as e:
                logger.warning("Bucket analysis error (%s): %s", analyzer.__name__, e)

        completed = self._ensure_checklist_completeness(artifact, results)
        return self._resolve_cannot_verify(artifact, completed)

    def _ensure_checklist_completeness(
        self,
        artifact: PageArtifact,
        results: list[CheckpointResult],
    ) -> list[CheckpointResult]:
        evidence_refs = [item for item in [artifact.dom_evidence_id, artifact.screenshot_evidence_id] if item]
        by_id: dict[str, CheckpointResult] = {}
        for item in results:
            by_id.setdefault(item.checkpoint_id, item)

        missing = [meta.checkpoint_id for meta in CHECKPOINTS if meta.checkpoint_id not in by_id]
        if missing:
            logger.warning("Checklist completeness guard inserted %d fallback results.", len(missing))

        completed: list[CheckpointResult] = []
        for meta in CHECKPOINTS:
            existing = by_id.get(meta.checkpoint_id)
            if existing is not None:
                completed.append(existing)
                continue

            fallback_status = (
                CheckpointStatus.NOT_APPLICABLE
                if self.cannot_verify_policy == "pass_leaning"
                else CheckpointStatus.CANNOT_VERIFY
            )
            fallback_rationale = (
                "Checklist completeness guard: analyzer output missing for this checkpoint; "
                "classified as Not applicable under pass-leaning policy."
                if fallback_status == CheckpointStatus.NOT_APPLICABLE
                else "Checklist completeness guard: analyzer output missing for this checkpoint."
            )
            completed.append(
                CheckpointResult(
                    checkpoint_id=meta.checkpoint_id,
                    bucket=meta.bucket,
                    status=fallback_status,
                    applicable=True,
                    page_url=artifact.url,
                    selector_or_target=None,
                    evidence_refs=evidence_refs,
                    rationale=fallback_rationale,
                    manual_required=meta.manual_component,
                )
            )
        return completed

    def _resolve_cannot_verify(
        self,
        artifact: PageArtifact,
        results: list[CheckpointResult],
    ) -> list[CheckpointResult]:
        if self.cannot_verify_policy != "pass_leaning":
            return results

        resolved: list[CheckpointResult] = []
        for finding in results:
            if finding.status != CheckpointStatus.CANNOT_VERIFY:
                resolved.append(finding)
                continue

            checkpoint_id = finding.checkpoint_id
            rationale_lower = (finding.rationale or "").strip().lower()

            if checkpoint_id in _CROSS_PAGE_NOT_APPLICABLE:
                finding.status = CheckpointStatus.NOT_APPLICABLE
                finding.rationale = self._policy_note(
                    finding.rationale,
                    "Not applicable",
                    "cross-page criterion",
                )
            elif checkpoint_id in _EXPLICIT_RISK_FAIL:
                finding.status = CheckpointStatus.FAIL
                finding.rationale = self._policy_note(
                    finding.rationale,
                    "Fail",
                    "explicit risk-sensitive checkpoint",
                )
            elif self._has_risk_signal(rationale_lower, artifact):
                finding.status = CheckpointStatus.FAIL
                finding.rationale = self._policy_note(
                    finding.rationale,
                    "Fail",
                    "risk pattern detected in automated evidence",
                )
            else:
                finding.status = CheckpointStatus.PASS
                finding.rationale = self._policy_note(
                    finding.rationale,
                    "Pass",
                    "no explicit risk pattern detected (pass-leaning policy)",
                )
            resolved.append(finding)
        return resolved

    def _has_risk_signal(self, rationale_lower: str, artifact: PageArtifact) -> bool:
        if _RISK_SIGNAL_RE.search(rationale_lower or ""):
            return True

        # Fallback signal from page text for risk-heavy patterns.
        html_lower = (artifact.html or "").lower()
        risk_tokens = (
            "captcha",
            "gesture",
            "drag",
            "onmousedown",
            "timeout",
            "settimeout",
            "carousel",
            "animation",
            "marquee",
            "blink",
        )
        return any(token in html_lower for token in risk_tokens)

    def _policy_note(self, original: str, to_status: str, reason: str) -> str:
        prefix = (original or "").strip()
        suffix = f"CV policy (pass_leaning): reclassified to {to_status} ({reason})."
        if not prefix:
            return suffix
        return f"{prefix} {suffix}"

    def summarize_findings(self, results: list[CheckpointResult]) -> dict[str, Any]:
        total = len(results)
        by_status = {}
        for r in results:
            s = r.status.value
            by_status[s] = by_status.get(s, 0) + 1

        failures = [
            {
                "checkpoint": r.checkpoint_id,
                "rationale": r.rationale,
                "page": r.page_url,
            }
            for r in results
            if r.status == CheckpointStatus.FAIL
        ]

        return {
            "total_checks": total,
            "pass": by_status.get("Pass", 0),
            "fail": by_status.get("Fail", 0),
            "cannot_verify": by_status.get("Cannot verify automatically", 0),
            "not_applicable": by_status.get("Not applicable", 0),
            "failures": failures,
        }
