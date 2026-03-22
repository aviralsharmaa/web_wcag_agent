from __future__ import annotations


class CSSStressWorker:
    """Consumes precomputed rendering metrics and applies deterministic defaults."""

    def analyze(self, current_metrics: dict) -> dict:
        metrics = dict(current_metrics)
        defaults = {
            "reflow_ok": None,
            "resize_text_ok": None,
            "text_spacing_ok": None,
            "hover_focus_ok": None,
            "orientation_locked": False,
        }
        for key, value in defaults.items():
            metrics.setdefault(key, value)
        return metrics
