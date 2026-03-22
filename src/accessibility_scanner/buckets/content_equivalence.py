from __future__ import annotations

from ..html_utils import DOMSnapshot
from ..models import CheckpointResult, CheckpointStatus, PageArtifact
from ..workers.common import accessible_name
from .base import result


def analyze_content_equivalence(page: PageArtifact) -> list[CheckpointResult]:
    snapshot = DOMSnapshot.from_html(page.html)
    findings: list[CheckpointResult] = []

    images = snapshot.find("img")
    svgs = snapshot.find("svg")
    canvas = snapshot.find("canvas")
    image_inputs = [node for node in snapshot.find("input") if node.attrs.get("type", "").lower() == "image"]
    icon_buttons = [
        node
        for node in snapshot.find("button")
        if not snapshot.descendants_text(node)
        and ("icon" in node.attrs.get("class", "").lower() or bool(node.attrs.get("aria-label")) is False)
    ]
    non_text_total = len(images) + len(svgs) + len(canvas) + len(image_inputs) + len(icon_buttons)

    missing_alt = [img for img in images if "alt" not in img.attrs]
    empty_alt_meaningful = [
        img
        for img in images
        if img.attrs.get("alt", "").strip() == ""
        and not snapshot.has_ancestor_tag(img, {"a", "button"})
        and "decorative" not in img.attrs.get("class", "").lower()
    ]
    unnamed_controls = [node for node in image_inputs + icon_buttons if not accessible_name(snapshot, node)]

    if non_text_total == 0:
        findings.append(result("1.1.1", CheckpointStatus.NOT_APPLICABLE, page, "No non-text content detected."))
    elif missing_alt or unnamed_controls:
        findings.append(
            result(
                "1.1.1",
                CheckpointStatus.FAIL,
                page,
                f"Missing text alternatives: {len(missing_alt)} images without alt and {len(unnamed_controls)} unnamed controls.",
            )
        )
    elif empty_alt_meaningful:
        findings.append(
            result(
                "1.1.1",
                CheckpointStatus.FAIL,
                page,
                f"Potential meaningful images using empty alt text: {len(empty_alt_meaningful)}.",
            )
        )
    else:
        findings.append(
            result(
                "1.1.1",
                CheckpointStatus.CANNOT_VERIFY,
                page,
                "Text alternatives are present, but equivalence quality requires manual review.",
            )
        )

    media = page.media_metadata
    videos = media.get("video_count", 0)
    audios = media.get("audio_count", 0)
    captions = media.get("caption_track_count", 0)
    descriptions = media.get("description_track_count", 0)

    if videos == 0 and audios == 0:
        findings.append(result("1.2.1", CheckpointStatus.NOT_APPLICABLE, page, "No prerecorded media found."))
    else:
        transcript_hint = "transcript" in page.html.lower()
        if transcript_hint:
            findings.append(
                result(
                    "1.2.1",
                    CheckpointStatus.CANNOT_VERIFY,
                    page,
                    "Transcript/media alternative references detected; equivalence needs manual validation.",
                )
            )
        else:
            findings.append(
                result(
                    "1.2.1",
                    CheckpointStatus.FAIL,
                    page,
                    "Prerecorded media found without transcript/media alternative evidence.",
                )
            )

    if videos == 0:
        findings.append(result("1.2.2", CheckpointStatus.NOT_APPLICABLE, page, "No prerecorded video found."))
        findings.append(result("1.2.3", CheckpointStatus.NOT_APPLICABLE, page, "No prerecorded synchronized media found."))
        findings.append(result("1.2.5", CheckpointStatus.NOT_APPLICABLE, page, "No prerecorded video found."))
    else:
        if captions > 0:
            findings.append(
                result(
                    "1.2.2",
                    CheckpointStatus.CANNOT_VERIFY,
                    page,
                    "Caption tracks detected; accuracy/completeness requires manual review.",
                )
            )
        else:
            findings.append(result("1.2.2", CheckpointStatus.FAIL, page, "Video content missing caption tracks."))

        if descriptions > 0 or "audio description" in page.html.lower():
            findings.append(
                result(
                    "1.2.3",
                    CheckpointStatus.CANNOT_VERIFY,
                    page,
                    "Audio description/media alternative found; adequacy requires manual review.",
                )
            )
            findings.append(
                result(
                    "1.2.5",
                    CheckpointStatus.CANNOT_VERIFY,
                    page,
                    "Audio description track/reference detected; adequacy requires manual review.",
                )
            )
        else:
            findings.append(
                result(
                    "1.2.3",
                    CheckpointStatus.FAIL,
                    page,
                    "No audio description or media alternative found for prerecorded video.",
                )
            )
            findings.append(
                result("1.2.5", CheckpointStatus.FAIL, page, "No audio description track found for prerecorded video.")
            )

    is_live = bool(media.get("has_live_hint"))
    if not is_live:
        findings.append(result("1.2.4", CheckpointStatus.NOT_APPLICABLE, page, "No live media detected."))
    elif captions > 0:
        findings.append(
            result(
                "1.2.4",
                CheckpointStatus.CANNOT_VERIFY,
                page,
                "Live caption signals detected; timing/quality must be tested manually.",
            )
        )
    else:
        findings.append(result("1.2.4", CheckpointStatus.FAIL, page, "Live media appears present without caption track evidence."))

    text_image_candidates = page.render_metrics.get("ocr_text_image_candidates", [])
    if not images:
        findings.append(result("1.4.5", CheckpointStatus.NOT_APPLICABLE, page, "No image content detected."))
    elif text_image_candidates:
        findings.append(
            result(
                "1.4.5",
                CheckpointStatus.CANNOT_VERIFY,
                page,
                f"Found {len(text_image_candidates)} likely text-in-image assets; manual validation required.",
            )
        )
    else:
        findings.append(result("1.4.5", CheckpointStatus.PASS, page, "No text-in-image heuristics detected."))

    return findings
