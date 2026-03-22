from pathlib import Path

from accessibility_scanner.engine import LangGraphScanner
from accessibility_scanner.evidence_store import EvidenceStore
from accessibility_scanner.fetchers import StaticFetcher, StaticPage
from accessibility_scanner.models import PolicyMode, ScanRequest


def _request(auth_script_ref=None):
    return ScanRequest(
        start_urls=["https://example.gov/"],
        domain_scope="example.gov",
        max_depth=1,
        max_pages=5,
        auth_script_ref=auth_script_ref,
        policy_mode=PolicyMode.STRICT_GOV,
    )


def _pages():
    return {
        "https://example.gov/": StaticPage(
            html="""
            <html lang='en'><body>
              <nav><a href='/about'>About</a><a href='/contact'>Contact</a></nav>
              <img src='logo.png' alt='Government logo'>
              <form><label for='email'>Email</label><input id='email' name='email' autocomplete='email'></form>
            </body></html>
            """,
            title="Home",
            links=["https://example.gov/about", "https://example.gov/contact"],
            render_metrics={
                "reflow_ok": True,
                "resize_text_ok": True,
                "text_spacing_ok": True,
                "hover_focus_ok": True,
                "contrast_violations": [],
                "non_text_contrast_violations": [],
                "orientation_locked": False,
            },
            interaction_metrics={
                "keyboard_access_ok": True,
                "keyboard_trap_detected": False,
                "character_shortcuts_present": False,
                "focus_context_change_detected": False,
                "form_error_identification_ok": True,
                "status_messages_announced": True,
                "nav_order_signature": ["a", "a"],
            },
        ),
        "https://example.gov/about": StaticPage(
            html="""
            <html lang='en'><body>
              <nav><a href='/about'>About</a><a href='/contact'>Contact</a></nav>
              <h1>About</h1><button aria-label='Menu'></button>
            </body></html>
            """,
            title="About",
            links=[],
            render_metrics={
                "reflow_ok": True,
                "resize_text_ok": True,
                "text_spacing_ok": True,
                "hover_focus_ok": True,
                "contrast_violations": [],
                "non_text_contrast_violations": [],
                "orientation_locked": False,
            },
            interaction_metrics={
                "keyboard_access_ok": True,
                "keyboard_trap_detected": False,
                "character_shortcuts_present": False,
                "focus_context_change_detected": False,
                "status_messages_announced": True,
                "nav_order_signature": ["a", "a"],
            },
        ),
        "https://example.gov/contact": StaticPage(
            html="<html lang='en'><body><nav><a href='/about'>About</a><a href='/contact'>Contact</a></nav></body></html>",
            title="Contact",
            links=[],
            render_metrics={
                "reflow_ok": True,
                "resize_text_ok": True,
                "text_spacing_ok": True,
                "hover_focus_ok": True,
                "contrast_violations": [],
                "non_text_contrast_violations": [],
                "orientation_locked": False,
            },
            interaction_metrics={
                "keyboard_access_ok": True,
                "keyboard_trap_detected": False,
                "character_shortcuts_present": False,
                "focus_context_change_detected": False,
                "nav_order_signature": ["a", "a"],
            },
        ),
    }


def test_auth_setup_runs_once_and_report_emits(tmp_path: Path) -> None:
    auth_script = tmp_path / "auth.py"
    auth_script.write_text("def login(context, request):\n    return None\n", encoding="utf-8")

    fetcher = StaticFetcher(_pages())
    scanner = LangGraphScanner(fetcher=fetcher, evidence_store=EvidenceStore(root=str(tmp_path / "artifacts")))
    report = scanner.run(_request(auth_script_ref=str(auth_script)))

    assert fetcher.setup_calls == 1
    assert report.run_id
    assert (Path(report.artifacts_dir) / "scan-report.json").exists()


def test_e2e_deterministic_results_on_same_snapshot(tmp_path: Path) -> None:
    fetcher1 = StaticFetcher(_pages())
    scanner1 = LangGraphScanner(fetcher=fetcher1, evidence_store=EvidenceStore(root=str(tmp_path / "a1")))
    report1 = scanner1.run(_request())

    fetcher2 = StaticFetcher(_pages())
    scanner2 = LangGraphScanner(fetcher=fetcher2, evidence_store=EvidenceStore(root=str(tmp_path / "a2")))
    report2 = scanner2.run(_request())

    status_map_1 = {item.checkpoint_id: item.status.value for item in report1.checkpoint_results}
    status_map_2 = {item.checkpoint_id: item.status.value for item in report2.checkpoint_results}

    assert status_map_1 == status_map_2
    assert report1.strict_decision == report2.strict_decision
