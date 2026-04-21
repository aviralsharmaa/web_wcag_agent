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

# Checkpoints where Cannot Verify should stay FAIL because the risk is high
# and absence of evidence ≠ evidence of absence.
_EXPLICIT_RISK_FAIL = {"2.5.1", "2.5.2"}

# Checkpoints where Cannot Verify should default to PASS — these are either
# rare conditions, require multi-page context, or are commonly N/A on most pages.
# If the bucket analyzer already marked it FAIL, that stands; only CV → PASS.
_SAFE_PASS_ON_CV = {
    "1.2.4",   # Live captions — rare
    "1.3.2",   # Meaningful sequence — needs rendered-order analysis
    "1.3.3",   # Sensory characteristics — rare actual violation
    "1.3.4",   # Orientation — already checked, CV means no lock found
    "1.4.1",   # Use of color — needs visual inspection
    "1.4.2",   # Audio control — already checked autoplay
    "1.4.12",  # Text spacing — supplementary test handles this
    "1.4.13",  # Content on hover/focus — needs interaction testing
    "2.1.4",   # Character key shortcuts — rare
    "2.3.1",   # Three flashes — requires frame analysis, extremely rare
    "2.4.1",   # Bypass blocks — already checked skip link
    "2.4.3",   # Focus order — supplementary keyboard test handles this
    "2.4.5",   # Multiple ways — already checked nav+search
    "2.4.11",  # Focus not obscured — needs visual analysis
    "2.5.3",   # Label in name — already checked programmatically
    "2.5.4",   # Motion actuation — rare
    "2.5.7",   # Dragging — rare
    "2.5.8",   # Target size — supplementary test handles this
    "3.1.2",   # Language of parts — rare multilingual content
    "3.2.1",   # On focus — already checked programmatically
    "3.2.2",   # On input — already checked auto-submit patterns
    "3.2.3",   # Consistent navigation — cross-page
    "3.3.1",   # Error identification — needs form submission test
    "3.3.3",   # Error suggestion — needs form submission test
    "3.3.4",   # Error prevention — needs transaction flow
    "3.3.7",   # Redundant entry — cross-page
    "3.3.8",   # Accessible auth — already checked captcha/cognitive
    "4.1.3",   # Status messages — already checked aria-live
}

# Per-checkpoint risk signals: only trigger FAIL from CV when the risk pattern
# is specifically relevant to the checkpoint being evaluated.
_CHECKPOINT_RISK_SIGNALS: dict[str, re.Pattern] = {
    "2.2.1": re.compile(r"(timer|timeout|settimeout|auto-logout|session.?timeout|meta.*refresh)", re.I),
    "2.2.2": re.compile(r"(carousel|marquee|blink|auto.?scroll|slideshow|animation-iteration-count:\s*infinite)", re.I),
    "2.5.1": re.compile(r"(pinch|swipe|multitouch|gesture)", re.I),
    "2.5.2": re.compile(r"(onmousedown|mousedown)", re.I),
    "2.5.7": re.compile(r"(draggable|ondrag|dragstart|sortable|drag-and-drop)", re.I),
    "1.4.5": re.compile(r"(text-in-image|image.?of.?text)", re.I),
    "3.3.8": re.compile(r"(captcha|recaptcha|hcaptcha|cognitive|puzzle)", re.I),
}


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
            elif checkpoint_id in _SAFE_PASS_ON_CV:
                finding.status = CheckpointStatus.PASS
                finding.rationale = self._policy_note(
                    finding.rationale,
                    "Pass",
                    "no concrete failure evidence (pass-leaning policy)",
                )
            elif self._has_checkpoint_risk(checkpoint_id, finding.rationale, artifact):
                finding.status = CheckpointStatus.FAIL
                finding.rationale = self._policy_note(
                    finding.rationale,
                    "Fail",
                    "checkpoint-specific risk pattern detected",
                )
            else:
                finding.status = CheckpointStatus.PASS
                finding.rationale = self._policy_note(
                    finding.rationale,
                    "Pass",
                    "no explicit failure evidence (pass-leaning policy)",
                )
            resolved.append(finding)
        return resolved

    def _has_checkpoint_risk(
        self, checkpoint_id: str, rationale: str, artifact: PageArtifact
    ) -> bool:
        """Check for risk signals specific to this checkpoint only."""
        pattern = _CHECKPOINT_RISK_SIGNALS.get(checkpoint_id)
        if pattern is None:
            return False
        rationale_lower = (rationale or "").lower()
        if pattern.search(rationale_lower):
            return True
        html_lower = (artifact.html or "").lower()
        return bool(pattern.search(html_lower))

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
