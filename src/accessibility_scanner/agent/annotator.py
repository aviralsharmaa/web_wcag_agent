"""Annotates screenshots with bounding boxes around non-compliant elements."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# WCAG fix suggestions keyed by checkpoint prefix
FIX_SUGGESTIONS: dict[str, str] = {
    "1.1.1": "Add descriptive alt text to <img> tags. Decorative images use alt=\"\".",
    "1.3.1": "Associate <label for=\"id\"> with every form input, or use aria-label.",
    "1.3.3": "Avoid instructions that rely only on color/shape/position.",
    "1.3.5": "Add autocomplete attributes to personal data inputs (email, tel, etc.).",
    "1.4.3": "Ensure text has >= 4.5:1 contrast ratio (3:1 for large text).",
    "1.4.11": "Ensure UI components have >= 3:1 contrast against background.",
    "2.1.1": "Make all interactive elements reachable via Tab. Use <button>/<a> instead of <div onclick>.",
    "2.1.2": "Ensure focus can leave every component. Avoid trapping focus in modals without escape.",
    "2.4.1": "Add a 'Skip to main content' link as the first focusable element.",
    "2.4.7": "Add visible focus styles: outline, box-shadow, or border on :focus/:focus-visible.",
    "4.1.1": "Fix HTML parsing errors: close all tags, remove duplicate IDs.",
    "4.1.2": "Add aria-label or visible text to all interactive elements (buttons, links, inputs).",
    "4.1.3": "Use aria-live='polite' on status message containers for screen reader announcements.",
}

# Colors for different severity levels
COLORS = {
    "critical": (220, 38, 38),    # red
    "high": (234, 88, 12),        # orange
    "medium": (202, 138, 4),      # yellow
    "low": (37, 99, 235),         # blue
}

CHECKPOINT_SEVERITY: dict[str, str] = {
    "1.1.1": "critical", "2.1.1": "critical", "2.1.2": "critical",
    "1.3.1": "high", "2.4.7": "high", "4.1.2": "high",
    "1.4.3": "medium", "4.1.1": "medium", "1.3.3": "medium",
    "2.4.1": "medium", "1.4.11": "medium", "4.1.3": "low",
    "1.3.5": "low",
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


def annotate_screenshot(
    screenshot_path: str,
    violations: list[dict[str, Any]],
    output_path: str | None = None,
) -> str:
    """Draw bounding boxes and labels on a screenshot for each violation.

    Each violation dict should have:
        - checkpoint_id: str (e.g. "1.1.1")
        - bbox: dict with x, y, width, height (in CSS pixels)
        - element: str (e.g. "<img> src=logo.png")
        - rationale: str
        - selector: str (optional)
    """
    img = Image.open(screenshot_path).convert("RGBA")

    # Create overlay for semi-transparent boxes
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    draw = ImageDraw.Draw(img)

    font = _get_font(13)
    font_small = _get_font(11)
    label_font = _get_font(14)

    for i, v in enumerate(violations):
        bbox = v.get("bbox", {})
        x = bbox.get("x", 0)
        y = bbox.get("y", 0)
        w = bbox.get("width", 0)
        h = bbox.get("height", 0)

        if w <= 0 or h <= 0:
            continue

        checkpoint = v.get("checkpoint_id", "?")
        severity = CHECKPOINT_SEVERITY.get(checkpoint, "medium")
        color = COLORS[severity]
        element = v.get("element", "")[:50]
        rationale = v.get("rationale", "")[:80]
        fix = FIX_SUGGESTIONS.get(checkpoint, "Manual review required.")[:80]

        # Draw semi-transparent fill
        overlay_draw.rectangle(
            [x, y, x + w, y + h],
            fill=(*color, 40),
            outline=(*color, 200),
            width=3,
        )

        # Draw solid border
        draw.rectangle([x, y, x + w, y + h], outline=color, width=3)

        # Draw label background
        label = f"[{checkpoint}] {rationale}"
        fix_text = f"FIX: {fix}"

        # Calculate label dimensions
        label_bbox = draw.textbbox((0, 0), label, font=font)
        label_w = label_bbox[2] - label_bbox[0] + 12
        label_h = label_bbox[3] - label_bbox[1] + 8

        fix_bbox = draw.textbbox((0, 0), fix_text, font=font_small)
        fix_w = fix_bbox[2] - fix_bbox[0] + 12
        fix_h = fix_bbox[3] - fix_bbox[1] + 6

        total_h = label_h + fix_h + 2
        max_w = max(label_w, fix_w)

        # Position label above element, or below if no room
        label_x = x
        label_y = y - total_h - 4
        if label_y < 0:
            label_y = y + h + 4

        # Clamp to image bounds
        if label_x + max_w > img.width:
            label_x = max(0, img.width - max_w)

        # Background for label
        draw.rectangle(
            [label_x, label_y, label_x + max_w, label_y + total_h],
            fill=(*color, 230) if img.mode == "RGBA" else color,
        )

        # White text on colored background
        draw.text((label_x + 5, label_y + 3), label, fill=(255, 255, 255), font=font)
        draw.text(
            (label_x + 5, label_y + label_h + 1),
            fix_text,
            fill=(255, 255, 220),
            font=font_small,
        )

        # Number badge on the element
        badge_text = str(i + 1)
        badge_bbox = draw.textbbox((0, 0), badge_text, font=label_font)
        badge_w = badge_bbox[2] - badge_bbox[0] + 10
        badge_h = badge_bbox[3] - badge_bbox[1] + 6
        draw.ellipse(
            [x - 2, y - 2, x + badge_w, y + badge_h],
            fill=color,
        )
        draw.text((x + 4, y + 1), badge_text, fill=(255, 255, 255), font=label_font)

    # Composite overlay
    img = Image.alpha_composite(img, overlay).convert("RGB")

    # Draw legend at top
    _draw_legend(img)

    out = output_path or screenshot_path.replace(".png", "-annotated.png")
    img.save(out, "PNG")
    return out


def _draw_legend(img: Image.Image):
    """Draw severity legend at top-right."""
    draw = ImageDraw.Draw(img)
    font = _get_font(12)

    legend_items = [
        ("CRITICAL", COLORS["critical"]),
        ("HIGH", COLORS["high"]),
        ("MEDIUM", COLORS["medium"]),
        ("LOW", COLORS["low"]),
    ]

    x_start = img.width - 380
    y_start = 5

    draw.rectangle(
        [x_start - 5, y_start - 2, img.width - 5, y_start + 22],
        fill=(30, 30, 30),
    )

    x = x_start
    for label, color in legend_items:
        draw.rectangle([x, y_start + 3, x + 14, y_start + 17], fill=color)
        draw.text((x + 18, y_start + 3), label, fill=(255, 255, 255), font=font)
        x += 90


def get_element_bboxes(page, violations_selectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use Playwright page to get bounding boxes for violation elements.

    Each item should have: checkpoint_id, selector, rationale
    Returns list with bbox added.
    """
    results = []
    for v in violations_selectors:
        selector = v.get("selector", "")
        if not selector:
            continue

        try:
            el = page.query_selector(selector)
            if el:
                box = el.bounding_box()
                if box:
                    results.append({
                        **v,
                        "bbox": {
                            "x": int(box["x"]),
                            "y": int(box["y"]),
                            "width": int(box["width"]),
                            "height": int(box["height"]),
                        },
                    })
        except Exception:
            pass

    return results


def find_violations_on_page(page) -> list[dict[str, Any]]:
    """Detect non-compliant elements and their bounding boxes directly from the live page."""
    return page.evaluate("""() => {
        const violations = [];

        // 1.1.1 — Images missing alt
        document.querySelectorAll('img:not([alt])').forEach((el, i) => {
            if (i >= 8) return;
            const r = el.getBoundingClientRect();
            if (r.width > 5 && r.height > 5 && r.top < window.innerHeight * 2) {
                violations.push({
                    checkpoint_id: '1.1.1',
                    element: `<img> src=${(el.src || '').substring(0, 50)}`,
                    rationale: 'Image missing alt text',
                    selector: '',
                    bbox: { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) }
                });
            }
        });

        // 1.3.1 — Inputs without labels
        document.querySelectorAll('input:not([type="hidden"]), select, textarea').forEach((el, i) => {
            if (i >= 8) return;
            const id = el.id;
            const hasLabel = id && document.querySelector(`label[for="${id}"]`);
            const hasAria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
            if (!hasLabel && !hasAria) {
                const r = el.getBoundingClientRect();
                if (r.width > 5 && r.height > 5 && r.top < window.innerHeight * 2) {
                    violations.push({
                        checkpoint_id: '1.3.1',
                        element: `<${el.tagName.toLowerCase()}> name=${el.name || ''} type=${el.type || ''}`,
                        rationale: 'Form input missing associated label',
                        selector: '',
                        bbox: { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) }
                    });
                }
            }
        });

        // 2.4.7 — Focus-visible violations
        const focusable = Array.from(document.querySelectorAll(
            'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
        )).slice(0, 15);
        for (const el of focusable) {
            el.focus();
            const cs = getComputedStyle(el);
            const outlineOk = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
            const shadowOk = cs.boxShadow && cs.boxShadow !== 'none';
            if (!outlineOk && !shadowOk) {
                const r = el.getBoundingClientRect();
                if (r.width > 5 && r.height > 5 && r.top < window.innerHeight * 2) {
                    violations.push({
                        checkpoint_id: '2.4.7',
                        element: `<${el.tagName.toLowerCase()}> ${(el.innerText || '').substring(0, 30)}`,
                        rationale: 'No visible focus indicator (missing outline/box-shadow)',
                        selector: '',
                        bbox: { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) }
                    });
                }
            }
        }
        document.activeElement?.blur?.();

        // 4.1.2 — Buttons/links missing accessible name
        document.querySelectorAll('button, [role="button"], a[href]').forEach((el, i) => {
            if (i >= 10) return;
            const text = (el.innerText || '').trim();
            const ariaLabel = el.getAttribute('aria-label') || '';
            const title = el.getAttribute('title') || '';
            if (!text && !ariaLabel && !title) {
                const r = el.getBoundingClientRect();
                if (r.width > 5 && r.height > 5 && r.top < window.innerHeight * 2) {
                    violations.push({
                        checkpoint_id: '4.1.2',
                        element: `<${el.tagName.toLowerCase()}> class=${(el.className || '').substring(0, 30)}`,
                        rationale: 'Interactive element missing accessible name',
                        selector: '',
                        bbox: { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) }
                    });
                }
            }
        });

        // 1.4.3 — Low contrast (inline styles only)
        document.querySelectorAll('[style*="color"]').forEach((el, i) => {
            if (i >= 5) return;
            const cs = getComputedStyle(el);
            const color = cs.color;
            const bg = cs.backgroundColor;
            // Simple heuristic: if both are very similar light values
            if (color && bg && color === bg) {
                const r = el.getBoundingClientRect();
                if (r.width > 5 && r.height > 5) {
                    violations.push({
                        checkpoint_id: '1.4.3',
                        element: `<${el.tagName.toLowerCase()}> ${(el.innerText || '').substring(0, 30)}`,
                        rationale: 'Possible contrast violation (text color matches background)',
                        selector: '',
                        bbox: { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) }
                    });
                }
            }
        });

        return violations.slice(0, 20);
    }""") or []
