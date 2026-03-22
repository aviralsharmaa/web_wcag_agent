"""Analyzes a single screen for WCAG issues using the existing bucket pipeline."""
from __future__ import annotations

import logging
from typing import Any

from ..buckets import (
    analyze_content_equivalence,
    analyze_interaction_navigation,
    analyze_layout_perception,
    analyze_semantics_transaction,
)
from ..models import CheckpointResult, PageArtifact
from ..workers import DeterministicWorkerSuite

logger = logging.getLogger(__name__)


class ScreenAnalyzer:
    """Runs all four WCAG buckets on a single PageArtifact."""

    def __init__(self) -> None:
        self.workers = DeterministicWorkerSuite()

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

        return results

    def summarize_findings(self, results: list[CheckpointResult]) -> dict[str, Any]:
        from ..models import CheckpointStatus

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
