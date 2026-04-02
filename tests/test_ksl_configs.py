import json
from datetime import datetime
from pathlib import Path

from accessibility_scanner.agent.cli import APP_CONFIG_MAP
from accessibility_scanner.agent.flow_runner import AgenticFlowRunner


class _Page:
    def __init__(self, title: str) -> None:
        self._title = title

    def title(self) -> str:
        return self._title

    def query_selector(self, _selector: str):
        return None


def test_ksl_configs_are_registered() -> None:
    assert APP_CONFIG_MAP["KSLNEO"] == "config/ksl_neo.json"
    assert APP_CONFIG_MAP["KSLKINSITE"] == "config/ksl_kinsite.json"


def test_ksl_neo_config_contains_manual_otp_step() -> None:
    config_path = Path(APP_CONFIG_MAP["KSLNEO"])
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["start_url"] == "https://www.kotakneo.com/"
    assert config["flow_steps"][2]["actions"][0]["type"] == "manual"
    assert config["flow_steps"][3]["scan_scope"] == "post_login"
    assert config["analysis"]["manual_assist_on_stall"] is True
    assert config["analysis"]["exploration_profile"]["persona"]


def test_ksl_kinsite_config_contains_manual_recovery_and_explore() -> None:
    config_path = Path(APP_CONFIG_MAP["KSLKINSITE"])
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["start_url"] == "https://kie.kotak.com/kinsite"
    assert config["flow_steps"][1]["actions"][0]["type"] == "manual"
    assert config["flow_steps"][2]["actions"][0]["type"] == "explore"
    assert config["analysis"]["manual_assist_on_stall"] is True


def test_ksl_pre_login_mode_skips_post_login_explore() -> None:
    neo_runner = AgenticFlowRunner(config_path="config/ksl_neo.json", headless=True, scan_mode="pre_login")
    assert neo_runner._should_run_step(neo_runner.config["flow_steps"][3]) is False

    kinsite_runner = AgenticFlowRunner(
        config_path="config/ksl_kinsite.json",
        headless=True,
        scan_mode="pre_login",
    )
    assert kinsite_runner._should_run_step(kinsite_runner.config["flow_steps"][2]) is False


def test_ksl_neo_manual_completion_does_not_match_login_page() -> None:
    runner = AgenticFlowRunner(config_path="config/ksl_neo.json", headless=True)
    runner._page = _Page("Login")
    action = runner.config["flow_steps"][2]["actions"][0]

    assert runner._manual_completion_met(action, "https://neo.kotaksecurities.com/Login") is False
    runner._page = _Page("Kotak Neo")
    assert runner._manual_completion_met(action, "https://neo.kotaksecurities.com/Landing") is True


def test_ksl_kinsite_exploration_context_includes_persona() -> None:
    runner = AgenticFlowRunner(config_path="config/ksl_kinsite.json", headless=True)
    context = runner._build_exploration_context("https://kie.kotak.com/kinsite/dashboard", {"title": "Dashboard"})

    assert "real authenticated Kinsite user" in context
    assert "dashboard" in context.lower()


def test_run_dir_naming_uses_asset_and_daily_sequence(tmp_path: Path) -> None:
    runner = AgenticFlowRunner(config_path="config/ksl_neo.json", artifacts_root=str(tmp_path), headless=True)
    moment = datetime(2026, 4, 2, 10, 30, 0)

    first = runner._next_run_dir_name(moment)
    (tmp_path / first).mkdir(parents=True)
    second = runner._next_run_dir_name(moment)

    assert first == "KSLNEO-2-4-26-1"
    assert second == "KSLNEO-2-4-26-2"
