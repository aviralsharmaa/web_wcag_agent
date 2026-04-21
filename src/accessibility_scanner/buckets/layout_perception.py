from __future__ import annotations

import re

from ..html_utils import DOMSnapshot, visible_text
from ..models import CheckpointResult, CheckpointStatus, PageArtifact
from .base import result


# Only flag sensory characteristics when directional/color terms are used in
# instructional context — phrases like "click the button on the right" or
# "the red icon indicates error".  Bare words like "left" or "top" appear in
# normal content (e.g. CSS classes, navigation labels) and should not trigger.
SENSORY_RE = re.compile(
    r"(click\s+(the\s+)?(button|link|icon|item)\s+(on\s+the\s+)?(left|right|above|below|top|bottom)"
    r"|see\s+the\s+(left|right|above|below|top|bottom)"
    r"|located\s+(on\s+the\s+|to\s+the\s+)(left|right|above|below|top|bottom)"
    r"|the\s+(red|green|blue)\s+(button|icon|link|indicator|circle|dot)\b"
    r"|indicated\s+by\s+(the\s+)?(red|green|blue|color)"
    r"|marked\s+in\s+(red|green|blue)"
    r"|the\s+(round|square|triangle|circular|star-shaped)\s+(button|icon|link))",
    re.IGNORECASE,
)
COLOR_ONLY_RE = re.compile(
    r"\b(in red|in green|marked in color|highlighted in color|color indicates"
    r"|shown in red|shown in green|displayed in color|error.{0,10}red|required.{0,10}red)\b",
    re.IGNORECASE,
)


def analyze_layout_perception(page: PageArtifact) -> list[CheckpointResult]:
    snapshot = DOMSnapshot.from_html(page.html)
    text = visible_text(page.html)
    findings: list[CheckpointResult] = []
    metrics = page.render_metrics

    # -- 1.3.1 Info and Relationships: check multiple semantic structures --
    issues_131: list[str] = []

    # (a) Form labels
    unlabeled_inputs = _unlabeled_inputs(snapshot)
    if unlabeled_inputs:
        issues_131.append(f"{len(unlabeled_inputs)} form controls without associated labels")

    # (b) Navigation landmark
    html_lower = page.html.lower()
    has_nav_landmark = "<nav" in html_lower or 'role="navigation"' in html_lower
    if not has_nav_landmark:
        # Only flag if there are multiple links (i.e. there IS a navigation)
        links = snapshot.find("a")
        if len(links) >= 5:
            issues_131.append("Navigation region (<nav>) missing for header/menu links")

    # (c) Heading hierarchy
    h1s = snapshot.find("h1")
    h2s = snapshot.find("h2")
    h3s = snapshot.find("h3")
    h4s = snapshot.find("h4")
    h5s = snapshot.find("h5")
    h6s = snapshot.find("h6")
    all_headings = h1s + h2s + h3s + h4s + h5s + h6s
    if not all_headings and len(visible_text(page.html)) > 200:
        issues_131.append("No heading elements found — page lacks structural hierarchy")
    elif all_headings:
        # Check for heading level skips (e.g. h1 → h3 with no h2)
        levels = sorted(set(
            int(h.tag[1]) for h in all_headings if h.tag and len(h.tag) == 2 and h.tag[1].isdigit()
        ))
        if levels and levels[0] != 1:
            issues_131.append(f"Heading hierarchy starts at h{levels[0]} instead of h1")
        for i in range(len(levels) - 1):
            if levels[i + 1] - levels[i] > 1:
                issues_131.append(f"Heading level skip: h{levels[i]} → h{levels[i+1]}")
                break

    # (d) Main landmark
    has_main = "<main" in html_lower or 'role="main"' in html_lower
    if not has_main:
        issues_131.append("No <main> landmark — screen readers cannot identify primary content")

    # (e) Table structure — data tables should have <th> or <caption>
    tables = snapshot.find("table")
    for table in tables:
        table_class = table.attrs.get("class", "").lower()
        table_role = table.attrs.get("role", "").lower()
        if table_role == "presentation" or "layout" in table_class:
            continue  # skip layout tables
        ths = [n for n in snapshot.nodes if n.tag == "th" and snapshot.has_ancestor_tag(n, {"table"})]
        captions = [n for n in snapshot.nodes if n.tag == "caption"]
        if not ths and not captions:
            issues_131.append("Data table missing <th> headers or <caption>")
            break  # one example is enough

    # (f) Fieldset/legend for radio/checkbox groups
    radios = [n for n in snapshot.find("input") if n.attrs.get("type", "").lower() in ("radio", "checkbox")]
    if len(radios) >= 2:
        has_fieldset = "<fieldset" in html_lower
        if not has_fieldset:
            issues_131.append("Radio/checkbox group missing <fieldset> and <legend>")

    if issues_131:
        findings.append(
            result(
                "1.3.1",
                CheckpointStatus.FAIL,
                page,
                "Semantic structure issues: " + "; ".join(issues_131) + ".",
            )
        )
    else:
        findings.append(result("1.3.1", CheckpointStatus.PASS, page, "Basic semantic relationships and structure detected."))

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
                CheckpointStatus.PASS,
                page,
                "No sensory-only instructional text detected.",
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
        measurable = [v.get("ratio") for v in contrast_violations if isinstance(v.get("ratio"), (int, float))]
        worst_ratio = min(measurable) if measurable else None
        ratio_note = f" Worst observed ratio: {worst_ratio}:1." if worst_ratio is not None else ""
        findings.append(
            result(
                "1.4.3",
                CheckpointStatus.FAIL,
                page,
                (
                    f"Detected {len(contrast_violations)} text contrast violations "
                    "(AA thresholds: 4.5:1 normal text, 3:1 large text)."
                    f"{ratio_note}"
                ),
            )
        )
    else:
        findings.append(
            result(
                "1.4.3",
                CheckpointStatus.PASS,
                page,
                "No text contrast violations detected (AA thresholds: 4.5:1 normal text, 3:1 large text).",
            )
        )

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
        measurable = [v.get("ratio") for v in non_text_violations if isinstance(v.get("ratio"), (int, float))]
        worst_ratio = min(measurable) if measurable else None
        ratio_note = f" Worst observed ratio: {worst_ratio}:1." if worst_ratio is not None else ""
        findings.append(
            result(
                "1.4.11",
                CheckpointStatus.FAIL,
                page,
                f"Detected {len(non_text_violations)} non-text contrast violations (AA threshold: 3:1).{ratio_note}",
            )
        )
    else:
        findings.append(result("1.4.11", CheckpointStatus.PASS, page, "No non-text contrast violations detected (AA threshold: 3:1)."))

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
