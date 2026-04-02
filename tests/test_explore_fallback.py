from accessibility_scanner.agent.flow_runner import AgenticFlowRunner


class _DummyElement:
    def __init__(self, max_length: int = -1) -> None:
        self.max_length = max_length

    def evaluate(self, _script: str) -> dict:
        return {"maxLength": self.max_length, "inputMode": "", "type": "text"}


def test_unique_screen_key_is_stable_for_same_input(monkeypatch) -> None:
    runner = AgenticFlowRunner(config_path="config/hdfc_sky_login.json", headless=True)
    monkeypatch.setattr(runner, "_dom_fingerprint", lambda: "a" * 40)

    page_info = {
        "title": "Dashboard",
        "headings": [{"text": "Overview"}, {"text": "Holdings"}],
    }
    key1 = runner._make_unique_screen_key("https://example.gov/path?x=1#top", page_info)
    key2 = runner._make_unique_screen_key("https://example.gov/path?x=2#next", page_info)
    assert key1 == key2


def test_unique_screen_key_changes_with_dom_fingerprint(monkeypatch) -> None:
    runner = AgenticFlowRunner(config_path="config/hdfc_sky_login.json", headless=True)
    page_info = {"title": "Dashboard", "headings": [{"text": "Overview"}]}

    monkeypatch.setattr(runner, "_dom_fingerprint", lambda: "a" * 40)
    key1 = runner._make_unique_screen_key("https://example.gov/path", page_info)
    monkeypatch.setattr(runner, "_dom_fingerprint", lambda: "b" * 40)
    key2 = runner._make_unique_screen_key("https://example.gov/path", page_info)
    assert key1 != key2


def test_fallback_explore_prefers_unseen_nav_click() -> None:
    runner = AgenticFlowRunner(config_path="config/hdfc_sky_login.json", headless=True)
    decision = runner._decide_fallback_explore_action(
        current_url="https://example.gov/dashboard",
        page_info={"can_scroll": False},
        visible_elements=[
            {"tag": "button", "text": "Settings", "role": "button", "href": "", "id": "settings-btn"},
            {"tag": "a", "text": "Help", "role": "link", "href": "https://example.gov/help", "id": ""},
        ],
    )

    assert decision["action"] == "click"
    assert decision["index"] == 1


def test_validation_profile_uses_configured_credentials() -> None:
    runner = AgenticFlowRunner(config_path="config/hdfc_sky_login.json", headless=True)
    assert runner.validation_profile["login_id"] == "JAI"
    assert runner.validation_profile["otp"] == "7890"
    assert runner.validation_profile["pin"] == "1234"


def test_explore_fill_replaces_random_value_with_configured_login() -> None:
    runner = AgenticFlowRunner(config_path="config/hdfc_sky_login.json", headless=True)
    value = runner._resolve_explore_fill_value(
        reason="Fill random test email",
        element_info={"type": "email", "placeholder": "Email"},
        llm_value="random@example.com",
        element_handle=_DummyElement(),
    )
    assert value == "JAI"


def test_explore_fill_uses_sequential_otp_digits_for_single_char_fields() -> None:
    runner = AgenticFlowRunner(config_path="config/hdfc_sky_login.json", headless=True)
    value1 = runner._resolve_explore_fill_value(
        reason="Fill OTP digit",
        element_info={"placeholder": "OTP"},
        llm_value="1234",
        element_handle=_DummyElement(max_length=1),
    )
    value2 = runner._resolve_explore_fill_value(
        reason="Fill OTP digit",
        element_info={"placeholder": "OTP"},
        llm_value="1234",
        element_handle=_DummyElement(max_length=1),
    )
    assert value1 == "7"
    assert value2 == "8"
