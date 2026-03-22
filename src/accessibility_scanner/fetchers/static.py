from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import PageArtifact, ScanRequest
from .base import BaseFetcher


@dataclass
class StaticPage:
    html: str
    title: str = ""
    links: list[str] = field(default_factory=list)
    render_metrics: dict[str, Any] = field(default_factory=dict)
    interaction_metrics: dict[str, Any] = field(default_factory=dict)
    media_metadata: dict[str, Any] = field(default_factory=dict)


class StaticFetcher(BaseFetcher):
    def __init__(self, pages: dict[str, StaticPage], auth_token: str | None = None) -> None:
        self.pages = pages
        self.auth_token = auth_token
        self.setup_calls = 0

    def setup(self, request: ScanRequest) -> dict[str, Any] | None:
        self.setup_calls += 1
        if request.auth_script_ref:
            return {"session": "static", "auth_token": self.auth_token or "token"}
        return None

    def fetch_page(
        self,
        url: str,
        depth: int,
        request: ScanRequest,
        run_id: str,
    ) -> PageArtifact:
        page = self.pages.get(url)
        if page is None:
            raise ValueError(f"Static page not found for URL: {url}")
        return PageArtifact(
            url=url,
            depth=depth,
            html=page.html,
            title=page.title,
            links=page.links,
            render_metrics=dict(page.render_metrics),
            interaction_metrics=dict(page.interaction_metrics),
            media_metadata=dict(page.media_metadata),
        )

    def teardown(self) -> None:
        return None
