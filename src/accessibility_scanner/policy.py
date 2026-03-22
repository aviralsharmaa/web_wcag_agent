from __future__ import annotations

from collections import defaultdict

from .checkpoints import CHECKPOINT_MAP
from .models import (
    AggregatedCheckpoint,
    CheckpointResult,
    CheckpointStatus,
    PolicyMode,
)


def reduce_checkpoint_status(statuses: list[CheckpointStatus]) -> CheckpointStatus:
    if not statuses:
        return CheckpointStatus.NOT_APPLICABLE
    if CheckpointStatus.FAIL in statuses:
        return CheckpointStatus.FAIL
    if CheckpointStatus.CANNOT_VERIFY in statuses:
        return CheckpointStatus.CANNOT_VERIFY
    if all(item == CheckpointStatus.NOT_APPLICABLE for item in statuses):
        return CheckpointStatus.NOT_APPLICABLE
    if CheckpointStatus.PASS in statuses:
        return CheckpointStatus.PASS
    return CheckpointStatus.NOT_APPLICABLE


def aggregate_checkpoint_results(per_page_results: list[CheckpointResult]) -> list[AggregatedCheckpoint]:
    grouped: dict[str, list[CheckpointResult]] = defaultdict(list)
    for finding in per_page_results:
        grouped[finding.checkpoint_id].append(finding)

    aggregate: list[AggregatedCheckpoint] = []
    for checkpoint_id, rows in grouped.items():
        status = reduce_checkpoint_status([row.status for row in rows])
        applicable = any(row.applicable for row in rows) and status != CheckpointStatus.NOT_APPLICABLE
        evidence_refs: list[str] = []
        pages: list[str] = []
        rationale_parts: list[str] = []
        manual_required = CHECKPOINT_MAP[checkpoint_id].manual_component

        for row in rows:
            evidence_refs.extend(row.evidence_refs)
            pages.append(row.page_url)
            if row.rationale:
                rationale_parts.append(row.rationale)
            if row.manual_required:
                manual_required = True

        aggregate.append(
            AggregatedCheckpoint(
                checkpoint_id=checkpoint_id,
                bucket=rows[0].bucket,
                status=status,
                applicable=applicable,
                evidence_refs=sorted(set(evidence_refs)),
                pages=sorted(set(pages)),
                rationale=" | ".join(dict.fromkeys(rationale_parts))[:1200],
                manual_required=manual_required,
            )
        )

    return sorted(aggregate, key=lambda item: item.checkpoint_id)


def compute_totals(aggregate: list[AggregatedCheckpoint]) -> dict[str, int]:
    totals = {
        CheckpointStatus.PASS.value: 0,
        CheckpointStatus.FAIL.value: 0,
        CheckpointStatus.CANNOT_VERIFY.value: 0,
        CheckpointStatus.NOT_APPLICABLE.value: 0,
        "Applicable": 0,
    }
    for item in aggregate:
        totals[item.status.value] += 1
        if item.applicable:
            totals["Applicable"] += 1
    return totals


def policy_decision(aggregate: list[AggregatedCheckpoint], mode: PolicyMode) -> str:
    applicable = [item for item in aggregate if item.applicable]
    if not applicable:
        return "Non-compliant"

    if mode == PolicyMode.STRICT_GOV:
        if any(item.status in {CheckpointStatus.FAIL, CheckpointStatus.CANNOT_VERIFY} for item in applicable):
            return "Non-compliant"
        return "Compliant"

    if any(item.status == CheckpointStatus.FAIL for item in applicable):
        return "Non-compliant"
    return "Compliant"
