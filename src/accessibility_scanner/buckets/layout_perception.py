from __future__ import annotations

import re

from ..html_utils import DOMSnapshot, visible_text
from ..models import CheckpointResult, CheckpointStatus, PageArtifact
from .base import result


SENSORY_RE = re.compile(r"\b(left|right|above|below|red|green|blue|top|bottom)\b")
COLOR_ONLY_RE = re.compile(r"\b(in red|in green|marked in color|highlighted in color|color indicates)\b")


def analyze_layout_perception(page: PageArtifact) -> list[CheckpointResult]:
    snapshot = DOMSnapshot.from_html(page.html)
    text = visible_text(page.html)
    findings: list[CheckpointResult] = []
    metrics = page.render_metrics

    unlabeled_inputs = _unlabeled_inputs(snapshot)
    if unlabeled_inputs:
        findings.append(
            result(
                "1.3.1",
                CheckpointStatus.FAIL,
                page,
                f"Detected {len(unlabeled_inputs)} form controls without associated labels.",
            )
        )
    else:
        findings.append(result("1.3.1", CheckpointStatus.PASS, page, "Basic relationships and labels detected."))

    seq_metric = metrics.get("reading_sequence_ok")
    if seq_metric is None:
        findings.append(
            result(
                "1.3.2",
                CheckpointStatus.CANNOT_VERIFY,
                page,
                "Meaningful reading sequence requires manual or rendered-order verification.",
            )
        )
    else:
        status = CheckpointStatus.PASS if seq_metric else CheckpointStatus.FAIL
        findings.append(result("1.3.2", status, page, "Reading sequence metric evaluated."))

    if SENSORY_RE.search(text):
        findings.append(
            result(
                "1.3.3",
                CheckpointStatus.FAIL,
                page,
                "Detected likely sensory-direction/color-only instruction text.",
            )
        )
    else:
        findings.append(
            result(
                "1.3.3",
                CheckpointStatus.CANNOT_VERIFY,
                page,
                "No obvious sensory-only instruction text; contextual adequacy requires manual review.",
            )
        )

    if metrics.get("orientation_locked", False):
        findings.append(result("1.3.4", CheckpointStatus.FAIL, page, "Orientation lock detected."))
    else:
        findings.append(result("1.3.4", CheckpointStatus.PASS, page, "No orientation lock evidence."))

    input_purpose_missing = _input_purpose_missing(snapshot)
    if input_purpose_missing:
        findings.append(
            result(
                "1.3.5",
                CheckpointStatus.FAIL,
                page,
                f"Detected {len(input_purpose_missing)} personal-data fields missing autocomplete purpose.",
            )
        )
    else:
        findings.append(result("1.3.5", CheckpointStatus.PASS, page, "Input purpose tokens present for detected personal-data fields."))

    if COLOR_ONLY_RE.search(text):
        findings.append(result("1.4.1", CheckpointStatus.FAIL, page, "Detected likely color-only instruction."))
    else:
        findings.append(
            result(
                "1.4.1",
                CheckpointStatus.CANNOT_VERIFY,
                page,
                "Color dependency requires contextual manual review beyond static text heuristics.",
            )
        )

    autoplay_without_controls = metrics.get("autoplay_audio_without_controls")
    if autoplay_without_controls is None:
        media = page.media_metadata
        autoplay_without_controls = media.get("autoplay_media_count", 0) > 0 and "controls" not in page.html.lower()

    if autoplay_without_controls:
        findings.append(result("1.4.2", CheckpointStatus.FAIL, page, "Autoplay media detected without pause/stop controls."))
    elif page.media_metadata.get("autoplay_media_count", 0) == 0:
        findings.append(result("1.4.2", CheckpointStatus.NOT_APPLICABLE, page, "No autoplay media detected."))
    else:
        findings.append(result("1.4.2", CheckpointStatus.PASS, page, "Autoplay controls appear present."))

    contrast_violations = metrics.get("contrast_violations")
    if contrast_violations is None:
        findings.append(
            result("1.4.3", CheckpointStatus.CANNOT_VERIFY, page, "Contrast metrics unavailable for this page.")
        )
    elif contrast_violations:
        findings.append(
            result(
                "1.4.3",
                CheckpointStatus.FAIL,
                page,
                f"Detected {len(contrast_violations)} contrast violations from inline-style analysis.",
            )
        )
    else:
        findings.append(result("1.4.3", CheckpointStatus.PASS, page, "No contrast violations detected in analyzed styles."))

    findings.append(_metric_result(page, "1.4.4", "resize_text_ok", "Resize text metric unavailable."))
    findings.append(_metric_result(page, "1.4.10", "reflow_ok", "Reflow metric unavailable."))

    non_text_violations = metrics.get("non_text_contrast_violations")
    if non_text_violations is None:
        findings.append(
            result(
                "1.4.11",
                CheckpointStatus.CANNOT_VERIFY,
                page,
                "Non-text contrast metric unavailable.",
            )
        )
    elif non_text_violations:
        findings.append(
            result(
                "1.4.11",
                CheckpointStatus.FAIL,
                page,
                f"Detected {len(non_text_violations)} non-text contrast violations.",
            )
        )
    else:
        findings.append(result("1.4.11", CheckpointStatus.PASS, page, "No non-text contrast violations detected."))

    findings.append(_metric_result(page, "1.4.12", "text_spacing_ok", "Text spacing metric unavailable."))
    findings.append(_metric_result(page, "1.4.13", "hover_focus_ok", "Hover/focus behavior metric unavailable."))

    # -- 2.5.8  Target Size (Minimum) (NEW) ----------------------------
    small_targets = metrics.get("small_click_targets")
    if small_targets is not None:
        if small_targets:
            findings.append(
                result(
                    "2.5.8",
                    CheckpointStatus.FAIL,
                    page,
                    f"Detected {len(small_targets)} interactive elements smaller than 24×24 CSS pixels.",
                )
            )
        else:
            findings.append(result("2.5.8", CheckpointStatus.PASS, page, "All sampled interactive targets ≥ 24×24 CSS px."))
    else:
        findings.append(
            result("2.5.8", CheckpointStatus.CANNOT_VERIFY, page, "Target size metrics unavailable; requires rendered measurement.")
        )

    return findings


def _metric_result(page: PageArtifact, checkpoint_id: str, metric_key: str, missing_msg: str) -> CheckpointResult:
    value = page.render_metrics.get(metric_key)
    if value is None:
        return result(checkpoint_id, CheckpointStatus.CANNOT_VERIFY, page, missing_msg)
    status = CheckpointStatus.PASS if value else CheckpointStatus.FAIL
    return result(checkpoint_id, status, page, f"Metric `{metric_key}` evaluated.")


def _unlabeled_inputs(snapshot: DOMSnapshot) -> list[str]:
    labels_for = {label.attrs.get("for", "") for label in snapshot.find("label") if label.attrs.get("for")}
    unlabeled: list[str] = []
    for node in snapshot.find("input") + snapshot.find("select") + snapshot.find("textarea"):
        node_id = node.attrs.get("id", "")
        if node.attrs.get("type", "").lower() == "hidden":
            continue
        if node_id and node_id in labels_for:
            continue
        if node.attrs.get("aria-label") or node.attrs.get("aria-labelledby"):
            continue
        if snapshot.has_ancestor_tag(node, {"label"}):
            continue
        unlabeled.append(node_id or node.attrs.get("name", node.tag))
    return unlabeled


def _input_purpose_missing(snapshot: DOMSnapshot) -> list[str]:
    personal_tokens = ("name", "email", "phone", "tel", "address", "city", "zip", "postal", "country")
    missing: list[str] = []
    for node in snapshot.find("input"):
        field_hint = " ".join(
            [
                node.attrs.get("name", ""),
                node.attrs.get("id", ""),
                node.attrs.get("placeholder", ""),
                node.attrs.get("type", ""),
            ]
        ).lower()
        if any(token in field_hint for token in personal_tokens):
            if not node.attrs.get("autocomplete"):
                missing.append(node.attrs.get("name") or node.attrs.get("id") or "input")
    return missing
