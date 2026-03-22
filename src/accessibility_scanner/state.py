from __future__ import annotations

from typing import Any, TypedDict

from .models import CheckpointResult, CrawlTarget, PageArtifact, ScanRequest


class ScanState(TypedDict, total=False):
    request: ScanRequest
    run_id: str
    artifacts_dir: str
    frontier: list[CrawlTarget]
    visited: set[str]
    current_target: CrawlTarget | None
    crawl_complete: bool
    page_artifacts: dict[str, PageArtifact]
    per_page_results: list[CheckpointResult]
    aggregate_results: list[dict[str, Any]]
    evidence_index: dict[str, dict[str, Any]]
    policy_outputs: dict[str, Any]
    errors: list[str]
    auth_context: dict[str, Any] | None
    active_buckets: list[str]
    report_path: str | None
