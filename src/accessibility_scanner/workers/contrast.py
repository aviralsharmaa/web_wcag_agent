from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..html_utils import DOMSnapshot, parse_style


@dataclass
class ContrastResult:
    text_samples: list[dict[str, Any]]
    non_text_samples: list[dict[str, Any]]
    violations: list[dict[str, Any]]
    non_text_violations: list[dict[str, Any]]


class ContrastWorker:
    """Contrast checks using rendered computed styles with static fallback."""

    def analyze(self, html: str, computed_samples: dict[str, Any] | None = None) -> ContrastResult:
        if computed_samples and (computed_samples.get("text") or computed_samples.get("non_text")):
            text_samples = self._analyze_text_samples(computed_samples.get("text", []))
            non_text_samples = self._analyze_non_text_samples(computed_samples.get("non_text", []))
        else:
            text_samples, non_text_samples = self._analyze_inline_style_fallback(html)

        violations = [item for item in text_samples if item.get("pass") is False]
        non_text_violations = [item for item in non_text_samples if item.get("pass") is False]
        return ContrastResult(
            text_samples=text_samples,
            non_text_samples=non_text_samples,
            violations=violations,
            non_text_violations=non_text_violations,
        )

    def _analyze_text_samples(self, samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
        evaluated: list[dict[str, Any]] = []
        for sample in samples:
            category = (sample.get("category") or "normal_text").lower()
            required_ratio = 3.0 if category == "large_text" else 4.5
            evaluated.append(
                _evaluate_sample(
                    sample=sample,
                    required_ratio=required_ratio,
                    category=category if category in {"normal_text", "large_text"} else "normal_text",
                )
            )
        return evaluated

    def _analyze_non_text_samples(self, samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
        evaluated: list[dict[str, Any]] = []
        for sample in samples:
            category = (sample.get("category") or "ui_component").lower()
            if category not in {"ui_component", "graphical_object"}:
                category = "ui_component"
            evaluated.append(
                _evaluate_sample(
                    sample=sample,
                    required_ratio=3.0,
                    category=category,
                )
            )
        return evaluated

    def _analyze_inline_style_fallback(self, html: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Fallback to static HTML-only contrast checks when rendered metrics are unavailable."""
        snapshot = DOMSnapshot.from_html(html)
        text_samples: list[dict[str, Any]] = []
        non_text_samples: list[dict[str, Any]] = []

        for node in snapshot.find_by_attr(None, "style"):
            styles = parse_style(node.attrs.get("style", ""))
            fg = styles.get("color")
            bg = styles.get("background-color") or styles.get("background")
            if fg and bg:
                font_size = _parse_px(styles.get("font-size"))
                font_weight = _parse_font_weight(styles.get("font-weight"))
                large_text = bool(font_size and (font_size >= 24 or (font_size >= 18.5 and font_weight >= 700)))
                category = "large_text" if large_text else "normal_text"
                text_samples.append(
                    _evaluate_sample(
                        sample={
                            "selector": node.tag,
                            "category": category,
                            "foreground_color": fg,
                            "background_color": bg,
                            "font_size_px": font_size,
                            "font_weight": font_weight,
                            "style": node.attrs.get("style", ""),
                            "source": "inline_style_fallback",
                        },
                        required_ratio=3.0 if large_text else 4.5,
                        category=category,
                    )
                )

            if node.tag in {"button", "input", "select", "textarea", "a"}:
                border = styles.get("border-color")
                if border and bg:
                    non_text_samples.append(
                        _evaluate_sample(
                            sample={
                                "selector": node.tag,
                                "category": "ui_component",
                                "foreground_color": border,
                                "background_color": bg,
                                "style": node.attrs.get("style", ""),
                                "source": "inline_style_fallback",
                            },
                            required_ratio=3.0,
                            category="ui_component",
                        )
                    )
        return text_samples, non_text_samples


HEX_RE = re.compile(r"^#([0-9a-fA-F]{3,8})$")
RGB_RE = re.compile(r"^rgba?\(([^\)]+)\)$")


def _parse_px(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text.endswith("px"):
        text = text[:-2]
    try:
        return float(text)
    except ValueError:
        return None


def _parse_font_weight(value: Any) -> int:
    if value is None:
        return 400
    text = str(value).strip().lower()
    if text == "bold":
        return 700
    if text == "normal":
        return 400
    try:
        return int(float(text))
    except ValueError:
        return 400


def _evaluate_sample(sample: dict[str, Any], required_ratio: float, category: str) -> dict[str, Any]:
    fg_raw = sample.get("foreground_color")
    bg_raw = sample.get("background_color")
    fg = _parse_color(str(fg_raw)) if fg_raw else None
    bg = _parse_color(str(bg_raw)) if bg_raw else None
    ratio_value = _contrast_ratio_with_alpha(fg, bg)
    ratio_display = round(ratio_value, 2) if ratio_value is not None else None
    passed = None if ratio_value is None else ratio_value >= required_ratio
    return {
        "selector": sample.get("selector", ""),
        "category": category,
        "ratio": ratio_display,
        "required_ratio": required_ratio,
        "pass": passed,
        "foreground_color": _normalize_color(fg, bg),
        "background_color": _normalize_color(bg, None),
        "font_size_px": sample.get("font_size_px"),
        "font_weight": sample.get("font_weight"),
        "bbox": sample.get("bbox"),
        "text": sample.get("text"),
        "style": sample.get("style"),
        "source": sample.get("source", "computed_style"),
    }


def _parse_color(value: str) -> tuple[float, float, float, float] | None:
    value = value.strip().lower()
    if value in {"transparent"}:
        return (0.0, 0.0, 0.0, 0.0)
    if value in {"black", "#000", "#000000"}:
        return (0.0, 0.0, 0.0, 1.0)
    if value in {"white", "#fff", "#ffffff"}:
        return (255.0, 255.0, 255.0, 1.0)

    match = HEX_RE.match(value)
    if match:
        raw = match.group(1)
        if len(raw) in {3, 4}:
            raw = "".join(ch * 2 for ch in raw)
        if len(raw) == 6:
            return (float(int(raw[0:2], 16)), float(int(raw[2:4], 16)), float(int(raw[4:6], 16)), 1.0)
        if len(raw) == 8:
            return (
                float(int(raw[0:2], 16)),
                float(int(raw[2:4], 16)),
                float(int(raw[4:6], 16)),
                round(int(raw[6:8], 16) / 255, 4),
            )
        return None

    rgb = RGB_RE.match(value)
    if rgb:
        parts = [part.strip() for part in rgb.group(1).split(",")]
        if len(parts) not in {3, 4}:
            return None
        try:
            red = _parse_rgb_channel(parts[0])
            green = _parse_rgb_channel(parts[1])
            blue = _parse_rgb_channel(parts[2])
            alpha = float(parts[3]) if len(parts) == 4 else 1.0
            alpha = max(0.0, min(alpha, 1.0))
            return (red, green, blue, alpha)
        except ValueError:
            return None
    return None


def _parse_rgb_channel(value: str) -> float:
    if value.endswith("%"):
        pct = float(value[:-1])
        return max(0.0, min(255.0, (pct / 100.0) * 255.0))
    return max(0.0, min(255.0, float(value)))


def _normalize_color(
    rgba: tuple[float, float, float, float] | None,
    backdrop: tuple[float, float, float, float] | None,
) -> str | None:
    if rgba is None:
        return None
    color = rgba
    if backdrop is not None and rgba[3] < 1:
        color = _composite_rgba_on_rgba(rgba, backdrop)
    if backdrop is None and rgba[3] < 1:
        color = _composite_rgba_on_rgba(rgba, (255.0, 255.0, 255.0, 1.0))
    red = int(round(max(0.0, min(255.0, color[0]))))
    green = int(round(max(0.0, min(255.0, color[1]))))
    blue = int(round(max(0.0, min(255.0, color[2]))))
    return f"#{red:02x}{green:02x}{blue:02x}"


def _composite_rgba_on_rgba(
    foreground: tuple[float, float, float, float],
    background: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    fg_r, fg_g, fg_b, fg_a = foreground
    bg_r, bg_g, bg_b, bg_a = background
    out_alpha = fg_a + bg_a * (1 - fg_a)
    if out_alpha <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    out_r = ((fg_r * fg_a) + (bg_r * bg_a * (1 - fg_a))) / out_alpha
    out_g = ((fg_g * fg_a) + (bg_g * bg_a * (1 - fg_a))) / out_alpha
    out_b = ((fg_b * fg_a) + (bg_b * bg_a * (1 - fg_a))) / out_alpha
    return (out_r, out_g, out_b, out_alpha)


def _linear(channel: float) -> float:
    s = channel / 255.0
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast_ratio(fg: tuple[int, int, int] | None, bg: tuple[int, int, int] | None) -> float | None:
    """Public helper retained for tests and compatibility."""
    if fg is None or bg is None:
        return None
    l1, l2 = _luminance(fg), _luminance(bg)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def _contrast_ratio_with_alpha(
    fg: tuple[float, float, float, float] | None,
    bg: tuple[float, float, float, float] | None,
) -> float | None:
    if fg is None or bg is None:
        return None
    effective_bg = _composite_rgba_on_rgba(bg, (255.0, 255.0, 255.0, 1.0))
    effective_fg = _composite_rgba_on_rgba(fg, effective_bg)
    fg_rgb = (
        int(round(effective_fg[0])),
        int(round(effective_fg[1])),
        int(round(effective_fg[2])),
    )
    bg_rgb = (
        int(round(effective_bg[0])),
        int(round(effective_bg[1])),
        int(round(effective_bg[2])),
    )
    l1, l2 = _luminance(fg_rgb), _luminance(bg_rgb)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)
