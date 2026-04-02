from accessibility_scanner.checklist_registry import load_checklist_spec_map, load_checklist_specs


def test_checklist_registry_loads_workbook_fields() -> None:
    specs = load_checklist_specs()

    assert len(specs) == 50
    assert specs[0].sc_id == "1.1.1"
    assert specs[0].sc_title == "Non-text Content"
    assert "run_id" in specs[0].output_json_field_list
    assert "accessible_name" in specs[0].output_json_field_list


def test_checklist_registry_builds_lookup_map() -> None:
    spec_map = load_checklist_spec_map()

    assert spec_map["4.1.3"].sc_title == "Status Messages"
    assert spec_map["4.1.3"].slug == "status_messages"
