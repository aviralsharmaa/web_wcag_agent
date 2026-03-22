from __future__ import annotations

from ..checkpoints import CHECKPOINT_MAP
from ..models import Bucket, CheckpointResult, CheckpointStatus, PageArtifact


def result(
    checkpoint_id: str,
    status: CheckpointStatus,
    page: PageArtifact,
    rationale: str,
    selector_or_target: str | None = None,
    evidence_refs: list[str] | None = None,
    applicable: bool | None = None,
    manual_required: bool | None = None,
) -> CheckpointResult:
    meta = CHECKPOINT_MAP[checkpoint_id]
    return CheckpointResult(
        checkpoint_id=checkpoint_id,
        bucket=meta.bucket,
        status=status,
        applicable=(status != CheckpointStatus.NOT_APPLICABLE) if applicable is None else applicable,
        page_url=page.url,
        selector_or_target=selector_or_target,
        evidence_refs=evidence_refs or [item for item in [page.dom_evidence_id, page.screenshot_evidence_id] if item],
        rationale=rationale,
        manual_required=meta.manual_component if manual_required is None else manual_required,
    )


def bucket_checkpoint(checkpoint_id: str) -> Bucket:
    return CHECKPOINT_MAP[checkpoint_id].bucket
