from accessibility_scanner.agent.screen_analyzer import ScreenAnalyzer
from accessibility_scanner.models import Bucket, CheckpointResult, CheckpointStatus, PageArtifact


def _artifact() -> PageArtifact:
    return PageArtifact(
        url="https://example.gov/page",
        depth=0,
        html="<html lang='en'><body><main>Sample</main></body></html>",
        title="Example",
        render_metrics={"computed_contrast_samples": {"text": [], "non_text": []}},
        interaction_metrics={"interactive_count": 0},
        media_metadata={},
    )


def _cv_result(checkpoint_id: str, rationale: str) -> CheckpointResult:
    return CheckpointResult(
        checkpoint_id=checkpoint_id,
        bucket=Bucket.INTERACTION_NAVIGATION,
        status=CheckpointStatus.CANNOT_VERIFY,
        applicable=True,
        page_url="https://example.gov/page",
        selector_or_target=None,
        evidence_refs=[],
        rationale=rationale,
        manual_required=False,
    )


def test_cross_page_cv_reclassified_to_not_applicable() -> None:
    analyzer = ScreenAnalyzer(cannot_verify_policy="pass_leaning")
    artifact = _artifact()
    resolved = analyzer._resolve_cannot_verify(artifact, [_cv_result("3.2.4", "Cross-page consistency requires manual verification.")])
    assert resolved[0].status == CheckpointStatus.NOT_APPLICABLE


def test_risk_sensitive_cv_reclassified_to_fail() -> None:
    analyzer = ScreenAnalyzer(cannot_verify_policy="pass_leaning")
    artifact = _artifact()
    resolved = analyzer._resolve_cannot_verify(artifact, [_cv_result("2.2.1", "Detected timer/timeout pattern in scripts.")])
    assert resolved[0].status == CheckpointStatus.FAIL


def test_non_risk_cv_reclassified_to_pass() -> None:
    analyzer = ScreenAnalyzer(cannot_verify_policy="pass_leaning")
    artifact = _artifact()
    resolved = analyzer._resolve_cannot_verify(artifact, [_cv_result("1.4.12", "Text spacing metric unavailable.")])
    assert resolved[0].status == CheckpointStatus.PASS


def test_completeness_guard_does_not_emit_cv_in_pass_leaning_mode() -> None:
    analyzer = ScreenAnalyzer(cannot_verify_policy="pass_leaning")
    artifact = _artifact()
    completed = analyzer._ensure_checklist_completeness(artifact, [])
    assert completed
    assert all(item.status != CheckpointStatus.CANNOT_VERIFY for item in completed)
