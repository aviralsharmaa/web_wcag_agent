from pathlib import Path

from PIL import Image

from accessibility_scanner.agent import annotator
from accessibility_scanner.agent.annotator import (
    annotate_screenshot,
    select_annotation_target,
    select_representative_failure,
)


def test_select_representative_failure_prefers_highest_severity() -> None:
    failures = [
        {"checkpoint": "1.4.11", "rationale": "non-text contrast fail"},
        {"checkpoint": "2.1.2", "rationale": "keyboard trap"},
        {"checkpoint": "4.1.2", "rationale": "missing name"},
    ]
    chosen = select_representative_failure(failures)
    assert chosen is not None
    assert chosen["checkpoint"] == "2.1.2"


def test_annotate_screenshot_draws_only_first_violation(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "annotated.png"
    Image.new("RGB", (220, 220), (255, 255, 255)).save(source)

    violations = [
        {
            "checkpoint_id": "1.1.1",
            "rationale": "Missing alt text",
            "bbox": {"x": 10, "y": 10, "width": 40, "height": 40},
        },
        {
            "checkpoint_id": "2.4.7",
            "rationale": "Missing focus style",
            "bbox": {"x": 140, "y": 140, "width": 40, "height": 40},
        },
    ]
    annotate_screenshot(str(source), violations, str(output))
    img = Image.open(output).convert("RGB")

    assert img.getpixel((15, 15)) != (255, 255, 255)
    assert img.getpixel((160, 160)) == (255, 255, 255)


def test_annotate_screenshot_always_uses_bbox_when_missing(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "annotated.png"
    Image.new("RGB", (120, 80), (255, 255, 255)).save(source)

    annotate_screenshot(
        str(source),
        [{"checkpoint_id": "1.4.3", "rationale": "Low contrast in login helper text"}],
        str(output),
    )
    img = Image.open(output).convert("RGB")

    # Top-left pixel is part of the coerced viewport bbox outline.
    assert img.getpixel((0, 0)) != (255, 255, 255)


def test_select_annotation_target_prefers_exact_match(monkeypatch) -> None:
    monkeypatch.setattr(
        annotator,
        "find_annotation_candidate_for_checkpoint",
        lambda page, checkpoint_id, rationale="": {
            "checkpoint_id": checkpoint_id,
            "rationale": rationale,
            "selector": "#exact",
            "tag": "button",
            "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
        },
    )
    monkeypatch.setattr(
        annotator,
        "_find_fallback_annotation_candidate",
        lambda page, checkpoint_id, rationale="": {
            "checkpoint_id": checkpoint_id,
            "selector": "#fallback",
            "tag": "div",
            "bbox": {"x": 1, "y": 1, "width": 5, "height": 5},
        },
    )

    result = select_annotation_target(page=object(), checkpoint_id="2.4.7", rationale="Focus indicator missing")
    assert result is not None
    assert result["selector"] == "#exact"
    assert result["map_quality"] == "exact"


def test_select_annotation_target_uses_fallback_when_exact_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        annotator,
        "find_annotation_candidate_for_checkpoint",
        lambda page, checkpoint_id, rationale="": None,
    )
    monkeypatch.setattr(
        annotator,
        "_find_fallback_annotation_candidate",
        lambda page, checkpoint_id, rationale="": {
            "checkpoint_id": checkpoint_id,
            "rationale": rationale,
            "selector": "body",
            "tag": "body",
            "fallback_tier": "viewport",
            "bbox": {"x": 0, "y": 0, "width": 100, "height": 80},
        },
    )

    result = select_annotation_target(page=object(), checkpoint_id="1.1.1", rationale="Missing alt text")
    assert result is not None
    assert result["selector"] == "body"
    assert result["map_quality"] == "fallback"


def test_annotation_label_contains_checkpoint_target_and_issue(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "annotated.png"
    Image.new("RGB", (180, 120), (255, 255, 255)).save(source)

    captured: dict[str, str] = {}

    def _capture_callout(draw, img_width, x, y, w, h, label, fix_text, color, font, font_small):
        captured["label"] = label

    monkeypatch.setattr(annotator, "_draw_callout", _capture_callout)
    annotate_screenshot(
        str(source),
        [
            {
                "checkpoint_id": "1.4.3",
                "rationale": "Low text contrast detected",
                "selector": ".login-caption",
                "tag": "p",
                "bbox": {"x": 8, "y": 8, "width": 24, "height": 18},
            }
        ],
        str(output),
    )

    assert captured["label"].startswith("[SC 1.4.3] .login-caption - Low text contrast detected")
