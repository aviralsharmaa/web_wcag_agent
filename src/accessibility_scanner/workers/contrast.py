from __future__ import annotations

import re
from dataclasses import dataclass

from ..html_utils import DOMSnapshot, parse_style


@dataclass
class ContrastResult:
    violations: list[dict]
    non_text_violations: list[dict]


class ContrastWorker:
    """Deterministic contrast checks from inline style declarations.

    This intentionally remains conservative: only explicit inline foreground/background
    values are evaluated to avoid false precision.
    """

    def analyze(self, html: str) -> ContrastResult:
        snapshot = DOMSnapshot.from_html(html)
        violations: list[dict] = []
        non_text_violations: list[dict] = []

        for node in snapshot.find_by_attr(None, "style"):
            styles = parse_style(node.attrs.get("style", ""))
            fg = styles.get("color")
            bg = styles.get("background") or styles.get("background-color")
            if fg and bg:
                ratio = contrast_ratio(_parse_color(fg), _parse_color(bg))
                if ratio is not None and ratio < 4.5:
                    violations.append({"tag": node.tag, "ratio": ratio, "style": node.attrs.get("style", "")})
            if node.tag in {"button", "input", "select", "textarea", "a"}:
                border = styles.get("border-color")
                if border and bg:
                    ratio = contrast_ratio(_parse_color(border), _parse_color(bg))
                    if ratio is not None and ratio < 3:
                        non_text_violations.append(
                            {"tag": node.tag, "ratio": ratio, "style": node.attrs.get("style", "")}
                        )

        return ContrastResult(violations=violations, non_text_violations=non_text_violations)


HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
RGB_RE = re.compile(r"^rgb\(([^\)]+)\)$")


def _parse_color(value: str) -> tuple[int, int, int] | None:
    value = value.strip().lower()
    if value in {"black", "#000", "#000000"}:
        return (0, 0, 0)
    if value in {"white", "#fff", "#ffffff"}:
        return (255, 255, 255)

    match = HEX_RE.match(value)
    if match:
        raw = match.group(1)
        if len(raw) == 3:
            raw = "".join(ch * 2 for ch in raw)
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))

    rgb = RGB_RE.match(value)
    if rgb:
        parts = [part.strip() for part in rgb.group(1).split(",")]
        if len(parts) != 3:
            return None
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            return None
    return None


def _linear(channel: float) -> float:
    s = channel / 255
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast_ratio(fg: tuple[int, int, int] | None, bg: tuple[int, int, int] | None) -> float | None:
    if fg is None or bg is None:
        return None
    l1, l2 = _luminance(fg), _luminance(bg)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return round((lighter + 0.05) / (darker + 0.05), 2)
