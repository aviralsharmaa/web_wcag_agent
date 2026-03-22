from __future__ import annotations

from urllib.parse import urljoin, urlparse, urlunparse


def canonicalize_url(url: str, base_url: str | None = None) -> str:
    merged = urljoin(base_url, url) if base_url else url
    parsed = urlparse(merged)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    # Fragment never influences crawl identity.
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def is_same_domain(url: str, domain_scope: str) -> bool:
    parsed = urlparse(url)
    scope = domain_scope.lower().lstrip(".")
    host = parsed.netloc.lower().split(":")[0]
    return host == scope or host.endswith(f".{scope}")


def should_enqueue(
    url: str,
    domain_scope: str,
    visited: set[str],
    frontier_urls: set[str],
    max_pages: int,
) -> bool:
    if len(visited) >= max_pages:
        return False
    if not is_same_domain(url, domain_scope):
        return False
    if url in visited or url in frontier_urls:
        return False
    return True
