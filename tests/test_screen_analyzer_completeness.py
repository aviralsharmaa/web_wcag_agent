from accessibility_scanner.agent.screen_analyzer import ScreenAnalyzer
from accessibility_scanner.checkpoints import CHECKPOINTS
from accessibility_scanner.models import PageArtifact


def test_screen_analyzer_outputs_complete_checkpoint_set() -> None:
    artifact = PageArtifact(
        url="https://example.gov/page",
        depth=0,
        html=(
            "<html lang='en'><head><title>Example</title></head>"
            "<body><main><h1>Example</h1><button aria-label='Open menu'></button></main></body></html>"
        ),
        title="Example",
        render_metrics={"orientation_locked": False, "computed_contrast_samples": {"text": [], "non_text": []}},
        interaction_metrics={"interactive_count": 1},
        media_metadata={},
    )

    results = ScreenAnalyzer().analyze(artifact)
    checkpoint_ids = [item.checkpoint_id for item in results]
    expected_ids = [item.checkpoint_id for item in CHECKPOINTS]

    assert len(results) == len(expected_ids)
    assert checkpoint_ids == expected_ids
