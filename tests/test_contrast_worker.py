from accessibility_scanner.workers.contrast import ContrastWorker, contrast_ratio


def test_contrast_ratio_formula_known_pair() -> None:
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == 21.0


def test_computed_contrast_thresholds_for_text_and_non_text() -> None:
    worker = ContrastWorker()
    computed_samples = {
        "text": [
            {
                "selector": "p.good",
                "category": "normal_text",
                "foreground_color": "#767676",
                "background_color": "#ffffff",
                "font_size_px": 16,
                "font_weight": 400,
            },
            {
                "selector": "p.bad",
                "category": "normal_text",
                "foreground_color": "#777777",
                "background_color": "#ffffff",
                "font_size_px": 16,
                "font_weight": 400,
            },
            {
                "selector": "h2.large",
                "category": "large_text",
                "foreground_color": "#949494",
                "background_color": "#ffffff",
                "font_size_px": 26,
                "font_weight": 700,
            },
        ],
        "non_text": [
            {
                "selector": "button.good",
                "category": "ui_component",
                "foreground_color": "#949494",
                "background_color": "#ffffff",
            },
            {
                "selector": "button.bad",
                "category": "ui_component",
                "foreground_color": "#969696",
                "background_color": "#ffffff",
            },
        ],
    }

    result = worker.analyze("<html></html>", computed_samples=computed_samples)

    assert len(result.text_samples) == 3
    assert len(result.non_text_samples) == 2
    assert len(result.violations) == 1
    assert len(result.non_text_violations) == 1

    assert result.violations[0]["selector"] == "p.bad"
    assert result.violations[0]["required_ratio"] == 4.5
    assert result.non_text_violations[0]["selector"] == "button.bad"
    assert result.non_text_violations[0]["required_ratio"] == 3.0
