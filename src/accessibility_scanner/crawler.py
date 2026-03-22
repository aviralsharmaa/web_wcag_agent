from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .models import CrawlTarget, ScanRequest
from .url_utils import canonicalize_url, should_enqueue


@dataclass
class CrawlQueue:
    queue: deque[CrawlTarget]

    @classmethod
    def from_start_urls(cls, request: ScanRequest) -> "CrawlQueue":
        seeded = [CrawlTarget(url=canonicalize_url(url), depth=0) for url in request.start_urls]
        return cls(queue=deque(seeded))

    def pop(self) -> CrawlTarget | None:
        if not self.queue:
            return None
        return self.queue.popleft()

    def push(self, target: CrawlTarget) -> None:
        self.queue.append(target)

    def urls(self) -> set[str]:
        return {item.url for item in self.queue}


class RobotsGate:
    def __init__(self) -> None:
        self._cache: dict[str, RobotFileParser] = {}

    def allowed(self, url: str, user_agent: str = "*") -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._cache:
            parser = RobotFileParser()
            parser.set_url(f"{root}/robots.txt")
            try:
                parser.read()
            except Exception:
                return True
            self._cache[root] = parser
        return self._cache[root].can_fetch(user_agent, url)


def expand_frontier(
    request: ScanRequest,
    visited: set[str],
    queue: CrawlQueue,
    base_url: str,
    links: Iterable[str],
    depth: int,
    robots_gate: RobotsGate,
) -> None:
    if depth >= request.max_depth:
        return

    frontier_urls = queue.urls()
    for raw in links:
        url = canonicalize_url(raw, base_url)
        if not should_enqueue(url, request.domain_scope, visited, frontier_urls, request.max_pages):
            continue
        if not robots_gate.allowed(url):
            continue
        queue.push(CrawlTarget(url=url, depth=depth + 1))
        frontier_urls.add(url)
