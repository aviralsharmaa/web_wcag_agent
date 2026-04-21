"""Single-annotation screenshot rendering utilities for WCAG findings."""
from __future__ import annotations

import logging
import re
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# WCAG fix suggestions keyed by checkpoint prefix.
FIX_SUGGESTIONS: dict[str, str] = {
    "1.1.1": "Add descriptive alt text to <img> tags. Decorative images use alt=\"\".",
    "1.3.1": "Associate <label for=\"id\"> with every form input, or use aria-label.",
    "1.3.3": "Avoid instructions that rely only on color/shape/position.",
    "1.3.5": "Add autocomplete attributes to personal data inputs (email, tel, etc.).",
    "1.4.3": "Ensure text has >= 4.5:1 contrast ratio (3:1 for large text).",
    "1.4.11": "Ensure UI components and graphics have >= 3:1 contrast.",
    "2.1.1": "Make all interactive elements reachable via Tab.",
    "2.1.2": "Ensure focus can leave every component and avoid traps.",
    "2.4.1": "Add a 'Skip to main content' link as first focusable element.",
    "2.4.7": "Add visible focus styles via outline/border/box-shadow.",
    "3.3.8": "Provide a non-cognitive alternative to captcha-based auth.",
    "4.1.1": "Fix HTML parsing errors and duplicate IDs.",
    "4.1.2": "Add accessible names to all interactive elements.",
    "4.1.3": "Use aria-live/status roles for dynamic status messages.",
}

COLORS = {
    "critical": (220, 38, 38),  # red
    "high": (234, 88, 12),  # orange
    "medium": (202, 138, 4),  # yellow
    "low": (37, 99, 235),  # blue
}

CHECKPOINT_SEVERITY: dict[str, str] = {
    "1.1.1": "critical",
    "2.1.1": "critical",
    "2.1.2": "critical",
    "3.3.8": "critical",
    "1.3.1": "high",
    "2.4.7": "high",
    "4.1.2": "high",
    "1.4.3": "medium",
    "4.1.1": "medium",
    "1.3.3": "medium",
    "2.4.1": "medium",
    "1.4.11": "medium",
    "4.1.3": "low",
    "1.3.5": "low",
}

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
ISSUE_SEVERITY_COLORS = {
    "critical": ((220, 0, 0, 20), (220, 0, 0), (170, 0, 0)),
    "high": ((255, 80, 0, 20), (255, 80, 0), (200, 60, 0)),
    "warning": ((220, 140, 0, 20), (220, 140, 0), (170, 110, 0)),
    "medium": ((220, 140, 0, 20), (220, 140, 0), (170, 110, 0)),
    "low": ((0, 120, 210, 20), (0, 120, 210), (0, 90, 170)),
    "pass": ((0, 140, 60, 15), (0, 140, 60), (0, 120, 50)),
}


def _get_font(size: int = 14):
    """Get a font, falling back to default if needed."""
    for path in [
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Monaco.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def severity_for_checkpoint(checkpoint_id: str) -> str:
    return CHECKPOINT_SEVERITY.get(checkpoint_id or "", "medium")


def select_representative_failure(failures: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose one deterministic, highest-priority checkpoint failure."""
    if not failures:
        return None

    def _sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
        checkpoint = item.get("checkpoint") or item.get("checkpoint_id") or ""
        severity = CHECKPOINT_SEVERITY.get(checkpoint, "medium")
        return (
            _SEVERITY_RANK.get(severity, 2),
            checkpoint,
            (item.get("rationale") or "")[:300],
        )

    return sorted(failures, key=_sort_key)[0]


def annotate_screenshot(
    screenshot_path: str,
    violations: list[dict[str, Any]],
    output_path: str | None = None,
) -> str:
    """Render one numbered annotation per violation on a screenshot."""
    img = Image.open(screenshot_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    draw = ImageDraw.Draw(img)

    font = _get_font(13)
    font_small = _get_font(11)

    # Track valid (non-fallback) bbox y-coordinates for focused crop
    valid_ys: list[int] = []
    valid_y2s: list[int] = []

    for issue_num, violation in enumerate(violations, start=1):
        checkpoint = violation.get("checkpoint_id", "?")
        severity = CHECKPOINT_SEVERITY.get(checkpoint, "medium")
        color = COLORS[severity]
        rationale = _sanitize_issue_text(violation.get("rationale", "") or "Accessibility issue detected.")
        fix = FIX_SUGGESTIONS.get(checkpoint, "Manual review required.")[:120]
        selector = (violation.get("selector") or "").strip()
        tag = (violation.get("tag") or _parse_tag_from_element(violation.get("element", "")) or "").strip()
        target = _target_descriptor(selector, tag)

        raw_bbox = violation.get("bbox") or {}
        is_fallback = (
            not _valid_bbox(raw_bbox)
            or violation.get("map_quality") == "fallback"
            or violation.get("fallback_tier") == "viewport"
        )
        bbox = _coerce_bbox(raw_bbox, img.width, img.height)
        x = int(bbox.get("x", 0))
        y = int(bbox.get("y", 0))
        w = int(bbox.get("width", 0))
        h = int(bbox.get("height", 0))

        # Track y-range of valid bboxes for focused crop
        if not is_fallback:
            valid_ys.append(y)
            valid_y2s.append(y + h)

        # Draw outline-only box (no filled overlay) so content stays visible
        overlay_draw.rectangle(
            [x - 2, y - 2, x + w + 2, y + h + 2],
            outline=(255, 255, 255, 220),
            width=2,
        )
        overlay_draw.rectangle(
            [x, y, x + w, y + h],
            fill=None,
            outline=(*color, 255),
            width=5,
        )
        draw.rectangle([x - 2, y - 2, x + w + 2, y + h + 2], outline=(255, 255, 255), width=2)
        draw.rectangle([x, y, x + w, y + h], outline=color, width=5)

        label = f"[{issue_num}][SC {checkpoint}] {target} - {rationale}"
        fix_text = f"FIX: {fix}"
        _draw_callout(draw, img.width, x, y, w, h, label, fix_text, color, font, font_small, issue_num)

    annotated = Image.alpha_composite(img, overlay).convert("RGB")

    # If the page is very tall, crop to a focused 2000-2500px region around annotations
    if annotated.height > 3000 and valid_ys:
        min_y = min(valid_ys)
        max_y = max(valid_y2s)
        mid_y = (min_y + max_y) // 2
        pad = 150
        crop_half = 1100  # half of ~2200px window
        crop_top = max(0, mid_y - crop_half)
        crop_bot = min(annotated.height, mid_y + crop_half)
        # Ensure padding around actual annotation bounds
        crop_top = max(0, min(crop_top, min_y - pad))
        crop_bot = min(annotated.height, max(crop_bot, max_y + pad))
        # Cap total height at 2500px
        if crop_bot - crop_top > 2500:
            crop_bot = crop_top + 2500
        annotated = annotated.crop((0, crop_top, annotated.width, crop_bot))

    out = output_path or screenshot_path.replace(".png", "-annotated.png")
    annotated.save(out, "PNG")
    return out


def crop_issue_region(
    screenshot_path: str,
    bbox: dict,
    padding: int = 200,
    output_path: str | None = None,
) -> str:
    """Crop a screenshot to a region around a specific bounding box."""
    img = Image.open(screenshot_path)
    x = max(0, bbox.get("x", 0) - padding)
    y = max(0, bbox.get("y", 0) - padding)
    x2 = min(img.width, bbox.get("x", 0) + bbox.get("width", 0) + padding)
    y2 = min(img.height, bbox.get("y", 0) + bbox.get("height", 0) + padding)
    if x2 - x < 100:
        x = max(0, x - 200)
        x2 = min(img.width, x2 + 200)
    if y2 - y < 100:
        y = max(0, y - 200)
        y2 = min(img.height, y2 + 200)
    cropped = img.crop((x, y, x2, y2))
    out = output_path or screenshot_path.replace(".png", f"-crop-{bbox.get('x',0)}-{bbox.get('y',0)}.png")
    cropped.save(out, "PNG")
    return out


def annotate_issue_collection(
    screenshot_path: str,
    issues: list[dict[str, Any]],
    annotated_path: str,
    crop_dir: str,
    checklist_slug: str = "",
) -> dict[str, list[tuple[str, str]]]:
    """Render Android-style issue overlays for a whole screen and per issue.

    checklist_slug — e.g. "01_non_text_content" — is prepended to crop/annotated
    filenames so they are globally unique across checklists.
    """
    img = Image.open(screenshot_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _get_font(20)

    crop_root = Path(crop_dir)
    crop_root.mkdir(parents=True, exist_ok=True)

    issue_crops: list[tuple[str, str]] = []
    issue_annotated: list[tuple[str, str]] = []
    screen_tag = _derive_screen_tag(annotated_path)
    prefix = f"{checklist_slug}_" if checklist_slug else ""

    for issue in issues:
        issue_id = str(issue.get("id") or "").strip()
        if not issue_id:
            continue

        issue_overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        issue_draw = ImageDraw.Draw(issue_overlay)

        bounds = issue.get("bounds")
        has_bounds, coords = _normalize_issue_bounds(bounds, img.size)
        if has_bounds:
            x1, y1, x2, y2 = coords
            _draw_issue_box_with_label(draw, issue, x1, y1, x2, y2, font)
            _draw_issue_box_with_label(issue_draw, issue, x1, y1, x2, y2, font)

            # Calculate label dimensions to include in crop
            lines = _issue_label_lines(issue, font)
            line_heights = [_text_size(font, line)[1] for line in lines]
            line_height = max(line_heights) if line_heights else 24
            label_h = max(32, line_height * len(lines) + 12)
            label_w = max(max(_text_size(font, line)[0] for line in lines) + 20, 220)
            label_top = max(0, y1 - label_h)

            # Crop must include: label (above element) + element + generous padding
            crop_pad = 60
            cx1 = max(0, x1 - crop_pad)
            cy1 = max(0, label_top - crop_pad)
            # Ensure right edge covers the full label width + padding
            cx2 = min(img.size[0], max(x2, x1 + label_w) + crop_pad + 30)
            cy2 = min(img.size[1], y2 + crop_pad + 120)

            # Enforce minimum crop size for good context
            min_w, min_h = 1000, 600
            cw, ch = cx2 - cx1, cy2 - cy1
            if cw < min_w:
                expand = (min_w - cw) // 2
                cx1 = max(0, cx1 - expand)
                cx2 = min(img.size[0], cx2 + expand)
                # If one side hit the image boundary, compensate on the other
                shortfall = min_w - (cx2 - cx1)
                if shortfall > 0:
                    cx1 = max(0, cx1 - shortfall)
                    shortfall = min_w - (cx2 - cx1)
                    if shortfall > 0:
                        cx2 = min(img.size[0], cx2 + shortfall)
            if ch < min_h:
                expand = (min_h - ch) // 2
                cy1 = max(0, cy1 - expand)
                cy2 = min(img.size[1], cy2 + expand)
                shortfall = min_h - (cy2 - cy1)
                if shortfall > 0:
                    cy1 = max(0, cy1 - shortfall)
                    shortfall = min_h - (cy2 - cy1)
                    if shortfall > 0:
                        cy2 = min(img.size[1], cy2 + shortfall)

            crop_path = crop_root / f"{prefix}{screen_tag}_{issue_id}_crop.png"
            annotated_crop = Image.alpha_composite(img, issue_overlay)
            annotated_crop.crop((cx1, cy1, cx2, cy2)).convert("RGB").save(crop_path, quality=95)
            issue_crops.append((issue_id, str(crop_path)))
        else:
            _draw_issue_label_only(issue_draw, issue, img.size, font)

        per_issue_path = Path(annotated_path).parent / f"{prefix}state_{screen_tag}__{issue_id}_annotated.png"
        Image.alpha_composite(img, issue_overlay).convert("RGB").save(per_issue_path, quality=95)
        issue_annotated.append((issue_id, str(per_issue_path)))

    Image.alpha_composite(img, overlay).convert("RGB").save(annotated_path, quality=95)
    return {"crops": issue_crops, "issue_annotated": issue_annotated}


def _draw_callout(
    draw: ImageDraw.ImageDraw,
    img_width: int,
    x: int,
    y: int,
    w: int,
    h: int,
    label: str,
    fix_text: str,
    color: tuple[int, int, int],
    font,
    font_small,
    issue_num: int = 1,
) -> None:
    label_bbox = draw.textbbox((0, 0), label, font=font)
    label_w = label_bbox[2] - label_bbox[0] + 12
    label_h = label_bbox[3] - label_bbox[1] + 8
    fix_bbox = draw.textbbox((0, 0), fix_text, font=font_small)
    fix_w = fix_bbox[2] - fix_bbox[0] + 12
    fix_h = fix_bbox[3] - fix_bbox[1] + 6
    total_h = label_h + fix_h + 2
    max_w = max(label_w, fix_w)

    label_x = x
    label_y = y - total_h - 4
    if label_y < 0:
        label_y = y + h + 4
    if label_x + max_w > img_width:
        label_x = max(0, img_width - max_w)

    draw.rectangle([label_x, label_y, label_x + max_w, label_y + total_h], fill=color)
    draw.text((label_x + 5, label_y + 3), label, fill=(255, 255, 255), font=font)
    draw.text((label_x + 5, label_y + label_h + 1), fix_text, fill=(255, 248, 220), font=font_small)

    badge_font = _get_font(15)
    draw.ellipse([x - 4, y - 4, x + 28, y + 28], fill=color)
    draw.text((x + 5, y + 4), str(issue_num), fill=(255, 255, 255), font=badge_font)


def _draw_issue_box_with_label(
    draw: ImageDraw.ImageDraw,
    issue: dict[str, Any],
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    font,
) -> None:
    severity = str(issue.get("severity") or "warning").lower()
    _fill, outline, label_bg = ISSUE_SEVERITY_COLORS.get(severity, ISSUE_SEVERITY_COLORS["warning"])
    # Draw outline-only border (no filled overlay) so the element stays visible
    draw.rectangle([x1, y1, x2, y2], fill=None, outline=outline + (255,), width=4)

    lines = _issue_label_lines(issue, font)
    line_heights = [_text_size(font, line)[1] for line in lines]
    line_height = max(line_heights) if line_heights else 24
    label_h = max(32, line_height * len(lines) + 12)
    label_w = max(max(_text_size(font, line)[0] for line in lines) + 20, 220)
    label_y = max(0, y1 - label_h)
    draw.rectangle([x1, label_y, x1 + label_w, label_y + label_h], fill=label_bg + (220,))
    for idx, line in enumerate(lines):
        draw.text((x1 + 6, label_y + 6 + idx * line_height), line, fill=(255, 255, 255, 255), font=font)


def _draw_issue_label_only(
    draw: ImageDraw.ImageDraw,
    issue: dict[str, Any],
    image_size: tuple[int, int],
    font,
) -> None:
    severity = str(issue.get("severity") or "warning").lower()
    _, outline, label_bg = ISSUE_SEVERITY_COLORS.get(severity, ISSUE_SEVERITY_COLORS["warning"])
    lines = _issue_label_lines(issue, font)
    line_heights = [_text_size(font, line)[1] for line in lines]
    line_height = max(line_heights) if line_heights else 24
    label_h = max(36, line_height * len(lines) + 16)
    label_w = min(
        image_size[0] - 24,
        max(max(_text_size(font, line)[0] for line in lines) + 20, 240),
    )
    x1, y1 = 12, 12
    x2, y2 = x1 + label_w, min(image_size[1] - 12, y1 + label_h)
    draw.rectangle([x1, y1, x2, y2], fill=label_bg + (220,), outline=outline + (255,), width=3)
    for idx, line in enumerate(lines):
        draw.text((x1 + 6, y1 + 6 + idx * line_height), line, fill=(255, 255, 255, 255), font=font)


def _issue_label_lines(issue: dict[str, Any], font) -> list[str]:
    xml_line = issue.get("xml_line")
    xml_label = f"L{xml_line}" if xml_line else "L?"
    base = f"{issue.get('id', 'ISSUE-???')} {issue.get('issue_type', 'issue')} {xml_label}"
    detail = _sanitize_issue_text(issue.get("detail", "") or issue.get("rationale", "") or "Accessibility issue detected.")
    wrapped = textwrap.wrap(f"{base} | {detail}", width=48)[:3]
    return wrapped or [base]


def _normalize_issue_bounds(bounds: Any, image_size: tuple[int, int]) -> tuple[bool, tuple[int, int, int, int]]:
    if isinstance(bounds, dict):
        bbox = _coerce_bbox(bounds, image_size[0], image_size[1])
        x1, y1 = bbox["x"], bbox["y"]
        x2, y2 = x1 + bbox["width"], y1 + bbox["height"]
        return True, (x1, y1, x2, y2)
    if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
        try:
            x1 = max(0, int(bounds[0][0]))
            y1 = max(0, int(bounds[0][1]))
            x2 = min(image_size[0], int(bounds[1][0]))
            y2 = min(image_size[1], int(bounds[1][1]))
            return (x2 > x1 and y2 > y1), (x1, y1, x2, y2)
        except Exception:
            pass
    return False, (0, 0, 0, 0)


def _text_size(font, text: str) -> tuple[int, int]:
    if hasattr(font, "getbbox"):
        box = font.getbbox(text)
        return box[2] - box[0], box[3] - box[1]
    return len(text) * 8, 20


def _derive_screen_tag(annotated_path: str) -> str:
    match = re.match(r"^state_(.+)_annotated\.png$", Path(annotated_path).name)
    return match.group(1) if match else "screen"


def _valid_bbox(bbox: dict[str, Any]) -> bool:
    return (
        bool(bbox)
        and float(bbox.get("width", 0) or 0) > 0
        and float(bbox.get("height", 0) or 0) > 0
    )


def _coerce_bbox(bbox: dict[str, Any], image_width: int, image_height: int) -> dict[str, int]:
    if _valid_bbox(bbox):
        x = max(0, min(int(float(bbox.get("x", 0))), max(0, image_width - 1)))
        y = max(0, min(int(float(bbox.get("y", 0))), max(0, image_height - 1)))
        width = max(1, int(float(bbox.get("width", 1))))
        height = max(1, int(float(bbox.get("height", 1))))
        if x + width > image_width:
            width = max(1, image_width - x)
        if y + height > image_height:
            height = max(1, image_height - y)
        return {"x": x, "y": y, "width": width, "height": height}
    return {"x": 0, "y": 0, "width": max(1, image_width), "height": max(1, image_height)}


def _parse_tag_from_element(element_repr: str) -> str:
    if not element_repr or not isinstance(element_repr, str):
        return ""
    candidate = element_repr.strip().lower()
    if candidate.startswith("<") and candidate.endswith(">"):
        candidate = candidate[1:-1]
    return candidate.strip()


def _target_descriptor(selector: str, tag: str) -> str:
    if selector:
        return selector[:80]
    if tag:
        return f"<{tag[:30]}>"
    return "<body>"


def _sanitize_issue_text(text: str) -> str:
    return (text or "Accessibility issue detected.").replace("\n", " ").strip()[:150]


def get_element_bboxes(page, violations_selectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use Playwright page to get bounding boxes for violation elements."""
    results = []
    for v in violations_selectors:
        selector = v.get("selector", "")
        if not selector:
            continue

        try:
            el = page.query_selector(selector)
            if not el:
                continue
            box = el.bounding_box()
            if box:
                results.append(
                    {
                        **v,
                        "bbox": {
                            "x": int(box["x"]),
                            "y": int(box["y"]),
                            "width": int(box["width"]),
                            "height": int(box["height"]),
                        },
                    }
                )
        except Exception:
            continue
    return results


def find_annotation_candidate_for_checkpoint(
    page,
    checkpoint_id: str,
    rationale: str = "",
) -> dict[str, Any] | None:
    """Find one best visible element candidate for a specific checkpoint."""
    try:
        return page.evaluate(
            """({ checkpointId, rationale }) => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    // Accept any rendered element on the full page, not just what's in the viewport
                    return r.width > 5 && r.height > 5;
                };
                const selectorFor = (el) => {
                    if (!el) return '';
                    if (el.id) return '#' + el.id;
                    const cls = (el.className && typeof el.className === 'string')
                        ? el.className.trim().split(/\\s+/).slice(0, 2).join('.')
                        : '';
                    return cls ? `${el.tagName.toLowerCase()}.${cls}` : el.tagName.toLowerCase();
                };
                const rgb = (value) => {
                    if (!value) return null;
                    const m = value.match(/rgba?\\(([^\\)]+)\\)/i);
                    if (!m) return null;
                    const p = m[1].split(',').map(s => parseFloat(s.trim()));
                    if (p.length < 3) return null;
                    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
                };
                const lum = (c) => {
                    const conv = (x) => {
                        const s = x / 255;
                        return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
                    };
                    return 0.2126 * conv(c.r) + 0.7152 * conv(c.g) + 0.0722 * conv(c.b);
                };
                const ratio = (a, b) => {
                    const l1 = lum(a);
                    const l2 = lum(b);
                    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
                };
                const backgroundFor = (el) => {
                    let cur = el;
                    while (cur) {
                        const bg = getComputedStyle(cur).backgroundColor;
                        const parsed = rgb(bg);
                        if (parsed && parsed.a > 0) return parsed;
                        cur = cur.parentElement;
                    }
                    return { r: 255, g: 255, b: 255, a: 1 };
                };
                const pack = (el, note) => {
                    const r = el.getBoundingClientRect();
                    const tag = el.tagName.toLowerCase();
                    // Add window.scrollY so coordinates align with full-page screenshots
                    return {
                        checkpoint_id: checkpointId,
                        rationale: rationale || note || 'WCAG finding',
                        selector: selectorFor(el),
                        element: `<${tag}>`,
                        tag,
                        bbox: {
                            x: Math.max(0, Math.round(r.x)),
                            y: Math.max(0, Math.round(r.y + window.scrollY)),
                            width: Math.max(1, Math.round(r.width)),
                            height: Math.max(1, Math.round(r.height)),
                        },
                    };
                };
                const findFirst = (nodes, pred) => {
                    for (const el of nodes) {
                        if (!isVisible(el)) continue;
                        if (!pred || pred(el)) return el;
                    }
                    return null;
                };

                if (checkpointId === '1.1.1') {
                    const target = findFirst(document.querySelectorAll('img:not([alt]), input[type="image"]'));
                    if (target) return pack(target, 'Image missing text alternative.');
                }

                if (checkpointId === '1.3.1' || checkpointId === '3.3.2') {
                    const controls = document.querySelectorAll('input:not([type="hidden"]), select, textarea');
                    const target = findFirst(controls, (el) => {
                        const id = el.id;
                        const hasLabel = id && document.querySelector(`label[for="${id}"]`);
                        const hasAria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
                        return !hasLabel && !hasAria;
                    });
                    if (target) return pack(target, 'Form control appears unlabeled.');
                }

                if (checkpointId === '1.3.5') {
                    const controls = document.querySelectorAll('input[name], input[id], input[placeholder]');
                    const target = findFirst(controls, (el) => {
                        const hint = `${el.name || ''} ${el.id || ''} ${el.placeholder || ''} ${el.type || ''}`.toLowerCase();
                        const personal = ['name', 'email', 'phone', 'tel', 'address', 'city', 'zip', 'postal', 'country'];
                        return personal.some(p => hint.includes(p)) && !el.getAttribute('autocomplete');
                    });
                    if (target) return pack(target, 'Personal-data field missing autocomplete purpose.');
                }

                if (checkpointId === '1.4.3') {
                    const textNodes = document.querySelectorAll(
                        'p,span,a,button,label,li,td,th,h1,h2,h3,h4,h5,h6,input,textarea,select,[role="button"],[role="link"]'
                    );
                    const target = findFirst(textNodes, (el) => {
                        const text = (el.innerText || el.textContent || '').trim();
                        if (!text) return false;
                        const cs = getComputedStyle(el);
                        const fg = rgb(cs.color);
                        const bg = backgroundFor(el);
                        if (!fg || !bg) return false;
                        const size = parseFloat(cs.fontSize) || 0;
                        const fwRaw = (cs.fontWeight || '').toString();
                        const fw = fwRaw === 'bold' ? 700 : (parseInt(fwRaw, 10) || 400);
                        const large = size >= 24 || (size >= 18.5 && fw >= 700);
                        const req = large ? 3 : 4.5;
                        return ratio(fg, bg) < req;
                    });
                    if (target) return pack(target, 'Low text contrast detected.');
                }

                if (checkpointId === '1.4.11') {
                    const nodes = document.querySelectorAll(
                        'button,input,select,textarea,a,[role="button"],[role="link"],svg,canvas'
                    );
                    const target = findFirst(nodes, (el) => {
                        const cs = getComputedStyle(el);
                        const bg = backgroundFor(el);
                        const border = rgb(cs.borderColor);
                        const fill = rgb(cs.backgroundColor);
                        const fg = border || fill;
                        if (!fg || !bg) return false;
                        return ratio(fg, bg) < 3;
                    });
                    if (target) return pack(target, 'Low non-text contrast detected.');
                }

                if (checkpointId === '2.4.7') {
                    const nodes = document.querySelectorAll(
                        'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
                    );
                    const target = findFirst(nodes, (el) => {
                        el.focus();
                        const cs = getComputedStyle(el);
                        const outlineOk = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
                        const shadowOk = cs.boxShadow && cs.boxShadow !== 'none';
                        return !outlineOk && !shadowOk;
                    });
                    document.activeElement?.blur?.();
                    if (target) return pack(target, 'Missing visible keyboard focus indicator.');
                }

                if (checkpointId === '4.1.2') {
                    const target = findFirst(document.querySelectorAll('button, [role="button"], a[href], input, select, textarea'), (el) => {
                        const text = (el.innerText || '').trim();
                        const aria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || '';
                        const title = el.getAttribute('title') || '';
                        return !text && !aria && !title;
                    });
                    if (target) return pack(target, 'Interactive element missing accessible name.');
                }

                if (checkpointId === '3.3.8') {
                    const target = findFirst(
                        document.querySelectorAll('[id*="captcha" i], [class*="captcha" i], iframe[src*="captcha" i], img[src*="captcha" i]')
                    );
                    if (target) return pack(target, 'Captcha/cognitive challenge detected.');
                }

                if (checkpointId === '1.2.1' || checkpointId === '1.2.2' || checkpointId === '1.2.3' || checkpointId === '1.2.4' || checkpointId === '1.2.5') {
                    const target = findFirst(document.querySelectorAll('video, audio, iframe[src*="youtube"], iframe[src*="vimeo"]'));
                    if (target) return pack(target, 'Media element requiring caption/description checks.');
                }

                if (checkpointId === '4.1.1') {
                    const target = document.querySelector('html') || document.body;
                    if (target && isVisible(target)) return pack(target, 'Markup/parsing issue requires structural fix.');
                }

                return null;
            }""",
            {"checkpointId": checkpoint_id, "rationale": rationale},
        )
    except Exception:
        return None


def select_annotation_target(
    page,
    checkpoint_id: str,
    rationale: str = "",
) -> dict[str, Any] | None:
    """Resolve one annotation target with deterministic fallback and concrete bbox."""
    exact = find_annotation_candidate_for_checkpoint(page, checkpoint_id, rationale)
    if exact and _valid_bbox(exact.get("bbox") or {}):
        exact["map_quality"] = "exact"
        exact["checkpoint_id"] = checkpoint_id or exact.get("checkpoint_id") or "?"
        exact["tag"] = exact.get("tag") or _parse_tag_from_element(exact.get("element", ""))
        return exact

    fallback = _find_fallback_annotation_candidate(page, checkpoint_id, rationale)
    if fallback and _valid_bbox(fallback.get("bbox") or {}):
        fallback["map_quality"] = "fallback"
        fallback["checkpoint_id"] = checkpoint_id or fallback.get("checkpoint_id") or "?"
        fallback["tag"] = fallback.get("tag") or _parse_tag_from_element(fallback.get("element", ""))
        return fallback

    return None


def _find_fallback_annotation_candidate(page, checkpoint_id: str, rationale: str = "") -> dict[str, Any] | None:
    try:
        return page.evaluate(
            """({ checkpointId, rationale }) => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    // Accept any rendered element on the full page, not just what's in the viewport
                    return r.width > 5 && r.height > 5;
                };
                const selectorFor = (el) => {
                    if (!el) return '';
                    if (el.id) return '#' + el.id;
                    const cls = (typeof el.className === 'string' ? el.className.trim() : '')
                        .split(/\\s+/).filter(Boolean).slice(0, 2).join('.');
                    return cls ? `${el.tagName.toLowerCase()}.${cls}` : el.tagName.toLowerCase();
                };
                const area = (el) => {
                    const r = el.getBoundingClientRect();
                    return Math.max(1, r.width * r.height);
                };
                const sortCandidates = (cands) => {
                    return cands.sort((a, b) => (
                        (b.area - a.area)
                        || (a.rect.top - b.rect.top)
                        || (a.rect.left - b.rect.left)
                        || a.selector.localeCompare(b.selector)
                    ));
                };
                const pack = (el, note, tier) => {
                    const r = el.getBoundingClientRect();
                    const tag = el.tagName.toLowerCase();
                    // Add window.scrollY so coordinates align with full-page screenshots
                    return {
                        checkpoint_id: checkpointId,
                        rationale: rationale || note || 'Accessibility issue detected.',
                        selector: selectorFor(el),
                        element: `<${tag}>`,
                        tag,
                        fallback_tier: tier,
                        bbox: {
                            x: Math.max(0, Math.round(r.x)),
                            y: Math.max(0, Math.round(r.y + window.scrollY)),
                            width: Math.max(1, Math.round(r.width)),
                            height: Math.max(1, Math.round(r.height)),
                        },
                    };
                };

                const controls = Array.from(document.querySelectorAll(
                    'input:not([type="hidden"]), select, textarea, button, a[href], [role="button"], [role="link"], [role="checkbox"], [role="radio"], [role="switch"], [role="tab"]'
                )).filter(isVisible);
                const failingControls = [];
                for (const el of controls) {
                    const tag = el.tagName.toLowerCase();
                    const id = el.id || '';
                    const safeId = id
                        ? ((window.CSS && CSS.escape)
                            ? CSS.escape(id)
                            : id.replace(/["\\\\]/g, '\\\\$&'))
                        : '';
                    const explicitLabel = safeId ? document.querySelector(`label[for="${safeId}"]`) : null;
                    const ariaName = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || '';
                    const title = el.getAttribute('title') || '';
                    const text = (el.innerText || '').trim();
                    const needsLabel = ['input', 'select', 'textarea'].includes(tag);
                    const named = !!(ariaName || title || text || explicitLabel);
                    if ((needsLabel && !named) || (!text && !ariaName && !title)) {
                        failingControls.push({
                            el,
                            area: area(el),
                            rect: el.getBoundingClientRect(),
                            selector: selectorFor(el),
                        });
                    }
                }
                if (failingControls.length) {
                    const picked = sortCandidates(failingControls)[0];
                    return pack(picked.el, 'Fallback to likely failing form/control element.', 'failing_form_control');
                }

                const interactive = Array.from(document.querySelectorAll(
                    'a[href], button, input, select, textarea, [role="button"], [role="link"], [role="tab"], [role="menuitem"], [tabindex]:not([tabindex="-1"])'
                )).filter(isVisible).map((el) => ({
                    el,
                    area: area(el),
                    rect: el.getBoundingClientRect(),
                    selector: selectorFor(el),
                }));
                if (interactive.length) {
                    const picked = sortCandidates(interactive)[0];
                    return pack(picked.el, 'Fallback to largest visible interactive element.', 'largest_interactive');
                }

                const media = Array.from(document.querySelectorAll('img, svg, canvas, video, picture, iframe'))
                    .filter(isVisible)
                    .map((el) => ({
                        el,
                        area: area(el),
                        rect: el.getBoundingClientRect(),
                        selector: selectorFor(el),
                    }));
                if (media.length) {
                    const picked = sortCandidates(media)[0];
                    return pack(picked.el, 'Fallback to largest visible media element.', 'largest_media');
                }

                const body = document.body || document.documentElement;
                if (body) {
                    const w = Math.max(1, Math.round(window.innerWidth || body.clientWidth || 1));
                    const h = Math.max(1, Math.round(window.innerHeight || body.clientHeight || 1));
                    return {
                        checkpoint_id: checkpointId,
                        rationale: rationale || 'Fallback to viewport region.',
                        selector: 'body',
                        element: '<body>',
                        tag: 'body',
                        fallback_tier: 'viewport',
                        bbox: { x: 0, y: 0, width: w, height: h },
                    };
                }
                return null;
            }""",
            {"checkpointId": checkpoint_id, "rationale": rationale},
        )
    except Exception:
        return None


def find_violations_on_page(page) -> list[dict[str, Any]]:
    """Backward-compatible helper that returns a compact multi-checkpoint sample."""
    checkpoints = ["1.1.1", "1.3.1", "1.4.3", "1.4.11", "2.4.7", "4.1.2"]
    findings: list[dict[str, Any]] = []
    for checkpoint in checkpoints:
        candidate = find_annotation_candidate_for_checkpoint(page, checkpoint)
        if candidate:
            findings.append(candidate)
    return findings
