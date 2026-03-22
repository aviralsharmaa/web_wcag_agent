from accessibility_scanner.url_utils import canonicalize_url, should_enqueue


def test_canonicalize_url_normalizes_path_and_fragment() -> None:
    assert canonicalize_url("https://Example.gov/path/#frag") == "https://example.gov/path"
    assert canonicalize_url("/about/", "https://example.gov") == "https://example.gov/about"


def test_should_enqueue_filters_domain_and_dedupes() -> None:
    visited = {"https://example.gov/"}
    frontier = {"https://example.gov/about"}

    assert not should_enqueue("https://other.gov/", "example.gov", visited, frontier, 10)
    assert not should_enqueue("https://example.gov/", "example.gov", visited, frontier, 10)
    assert not should_enqueue("https://example.gov/about", "example.gov", visited, frontier, 10)
    assert should_enqueue("https://example.gov/contact", "example.gov", visited, frontier, 10)
