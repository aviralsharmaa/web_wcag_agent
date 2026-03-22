from __future__ import annotations

from ..html_utils import DOMSnapshot


class OCRTextImageWorker:
    """Heuristic detector for likely text-in-image assets.

    True OCR is optional in v1; this worker surfaces candidates for manual/secondary checks.
    """

    def detect_candidates(self, html: str) -> list[dict]:
        snapshot = DOMSnapshot.from_html(html)
        candidates: list[dict] = []
        for img in snapshot.find("img"):
            src = img.attrs.get("src", "").lower()
            alt = img.attrs.get("alt", "").lower()
            tokens = f"{src} {alt}"
            if any(term in tokens for term in ("banner", "heading", "title", "text", "hero")):
                candidates.append({"src": src, "alt": alt})
        return candidates
