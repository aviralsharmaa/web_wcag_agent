from __future__ import annotations

from ..html_utils import DOMSnapshot
from .common import accessible_name, is_interactive


class AxeWorker:
    """Deterministic lightweight accessibility rule checks.

    This is not a replacement for axe-core runtime execution; it provides core static
    checks for v1 and can be supplemented by browser-side axe injections.
    """

    def analyze(self, html: str) -> dict:
        snapshot = DOMSnapshot.from_html(html)
        issues: list[dict] = []

        for img in snapshot.find("img"):
            if "alt" not in img.attrs:
                issues.append({"rule": "image-alt", "target": "img", "message": "img missing alt"})

        for node in snapshot.nodes:
            if is_interactive(node) and not accessible_name(snapshot, node):
                issues.append(
                    {
                        "rule": "control-name",
                        "target": node.tag,
                        "message": "Interactive control has no accessible name",
                    }
                )

        return {"issues": issues}
