from __future__ import annotations

from ..html_utils import DOMSnapshot
from .common import is_interactive


class KeyboardTraversalWorker:
    """Heuristic keyboard checks for static analysis.

    A real browser traversal can augment these metrics before bucket evaluation.
    """

    def analyze(self, html: str, existing: dict) -> dict:
        metrics = dict(existing)
        snapshot = DOMSnapshot.from_html(html)
        interactive = [node for node in snapshot.nodes if is_interactive(node)]
        if "keyboard_access_ok" not in metrics:
            # Heuristic: if clickable div/span with onclick and no tabindex exists, keyboard access is likely incomplete.
            clickable_non_semantic = [
                node
                for node in snapshot.nodes
                if node.tag in {"div", "span"} and "onclick" in node.attrs and "tabindex" not in node.attrs
            ]
            metrics["keyboard_access_ok"] = len(clickable_non_semantic) == 0
        metrics.setdefault("keyboard_trap_detected", False)
        metrics.setdefault("focus_context_change_detected", False)
        metrics.setdefault("character_shortcuts_present", False)
        metrics.setdefault("char_shortcuts_scoped", True)
        metrics.setdefault("focusable_count", len(interactive))
        if "nav_order_signature" not in metrics:
            # Use real focus trail if available from agentic probe
            focus_trail = metrics.get("focus_trail")
            if focus_trail:
                metrics["nav_order_signature"] = [e.get("tag", "") for e in focus_trail[:25]]
            else:
                metrics["nav_order_signature"] = [node.tag for node in interactive[:25]]
        return metrics
