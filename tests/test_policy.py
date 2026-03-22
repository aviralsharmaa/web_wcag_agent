from accessibility_scanner.models import (
    AggregatedCheckpoint,
    Bucket,
    CheckpointStatus,
    PolicyMode,
)
from accessibility_scanner.policy import policy_decision, reduce_checkpoint_status


def test_reduce_checkpoint_status_precedence() -> None:
    assert (
        reduce_checkpoint_status([CheckpointStatus.PASS, CheckpointStatus.FAIL, CheckpointStatus.CANNOT_VERIFY])
        == CheckpointStatus.FAIL
    )
    assert reduce_checkpoint_status([CheckpointStatus.PASS, CheckpointStatus.CANNOT_VERIFY]) == CheckpointStatus.CANNOT_VERIFY
    assert reduce_checkpoint_status([CheckpointStatus.NOT_APPLICABLE, CheckpointStatus.NOT_APPLICABLE]) == CheckpointStatus.NOT_APPLICABLE


def test_policy_decision_strict_vs_automation() -> None:
    aggregate = [
        AggregatedCheckpoint(
            checkpoint_id="1.1.1",
            bucket=Bucket.CONTENT_EQUIVALENCE,
            status=CheckpointStatus.PASS,
            applicable=True,
            evidence_refs=[],
            pages=["https://example.gov"],
            rationale="ok",
            manual_required=False,
        ),
        AggregatedCheckpoint(
            checkpoint_id="1.2.2",
            bucket=Bucket.CONTENT_EQUIVALENCE,
            status=CheckpointStatus.CANNOT_VERIFY,
            applicable=True,
            evidence_refs=[],
            pages=["https://example.gov"],
            rationale="manual",
            manual_required=True,
        ),
    ]

    assert policy_decision(aggregate, PolicyMode.STRICT_GOV) == "Non-compliant"
    assert policy_decision(aggregate, PolicyMode.AUTOMATION_ONLY) == "Compliant"
