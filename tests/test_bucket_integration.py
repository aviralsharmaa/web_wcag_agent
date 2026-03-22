from accessibility_scanner.buckets import (
    analyze_content_equivalence,
    analyze_interaction_navigation,
    analyze_layout_perception,
    analyze_semantics_transaction,
)
from accessibility_scanner.models import CheckpointStatus, PageArtifact


def _artifact(html: str) -> PageArtifact:
    return PageArtifact(
        url="https://example.gov/page",
        depth=0,
        html=html,
        title="Example",
        render_metrics={
            "reflow_ok": True,
            "resize_text_ok": True,
            "text_spacing_ok": True,
            "hover_focus_ok": True,
            "contrast_violations": [],
            "non_text_contrast_violations": [],
            "orientation_locked": False,
            "ocr_text_image_candidates": [],
        },
        interaction_metrics={
            "keyboard_access_ok": True,
            "keyboard_trap_detected": False,
            "character_shortcuts_present": False,
            "focus_context_change_detected": False,
            "form_error_identification_ok": True,
            "status_messages_announced": True,
            "transaction_review_step_ok": True,
            "nav_order_signature": ["a", "a", "button"],
        },
        media_metadata={
            "video_count": 0,
            "audio_count": 0,
            "caption_track_count": 0,
            "description_track_count": 0,
            "autoplay_media_count": 0,
            "has_live_hint": False,
            "parsing_errors": 0,
        },
    )


def _status_map(results):
    return {item.checkpoint_id: item.status for item in results}


def test_content_bucket_flags_missing_alt() -> None:
    page = _artifact("<html lang='en'><body><img src='x.png'></body></html>")
    statuses = _status_map(analyze_content_equivalence(page))
    assert statuses["1.1.1"] == CheckpointStatus.FAIL


def test_layout_bucket_runs_render_metrics() -> None:
    html = """
    <html lang='en'><body>
      <form><label for='email'>Email</label><input id='email' name='email' autocomplete='email' /></form>
      <p style='color:#000;background-color:#fff'>Readable</p>
    </body></html>
    """
    page = _artifact(html)
    statuses = _status_map(analyze_layout_perception(page))
    assert statuses["1.4.10"] == CheckpointStatus.PASS
    assert statuses["1.4.3"] == CheckpointStatus.PASS


def test_interaction_bucket_uses_keyboard_metrics() -> None:
    page = _artifact("<html lang='en'><body><button>Go</button></body></html>")
    statuses = _status_map(analyze_interaction_navigation(page))
    assert statuses["2.1.1"] == CheckpointStatus.PASS
    assert statuses["2.1.2"] == CheckpointStatus.PASS


def test_semantics_bucket_checks_lang_and_nrv() -> None:
    page = _artifact("<html lang='en'><body><button aria-label='Submit'></button></body></html>")
    statuses = _status_map(analyze_semantics_transaction(page))
    assert statuses["3.1.2"] == CheckpointStatus.CANNOT_VERIFY
    assert statuses["4.1.2"] == CheckpointStatus.PASS
