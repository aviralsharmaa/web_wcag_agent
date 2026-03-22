from accessibility_scanner.engine import LangGraphScanner
from accessibility_scanner.evidence_store import EvidenceStore
from accessibility_scanner.fetchers import StaticFetcher, StaticPage
from accessibility_scanner.models import ScanRequest


def test_checkpoint_overrides_disable_specific_checks(tmp_path) -> None:
    pages = {
        "https://example.gov/": StaticPage(
            html="<html lang='en'><body><img src='x.png'></body></html>",
            links=[],
        )
    }
    request = ScanRequest(
        start_urls=["https://example.gov/"],
        domain_scope="example.gov",
        max_depth=0,
        max_pages=1,
        checkpoint_overrides={"1.1.1": False},
    )

    scanner = LangGraphScanner(fetcher=StaticFetcher(pages), evidence_store=EvidenceStore(root=str(tmp_path / "artifacts")))
    report = scanner.run(request)

    checkpoint_ids = {item.checkpoint_id for item in report.per_page_results}
    assert "1.1.1" not in checkpoint_ids
