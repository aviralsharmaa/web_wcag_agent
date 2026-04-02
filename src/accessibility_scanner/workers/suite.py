from __future__ import annotations

from ..models import PageArtifact
from .axe import AxeWorker
from .contrast import ContrastWorker
from .css_stress import CSSStressWorker
from .keyboard import KeyboardTraversalWorker
from .media_metadata import MediaMetadataWorker
from .ocr_text_image import OCRTextImageWorker
from .parser_validator import ParserValidatorWorker


class DeterministicWorkerSuite:
    def __init__(self) -> None:
        self.axe = AxeWorker()
        self.contrast = ContrastWorker()
        self.css = CSSStressWorker()
        self.keyboard = KeyboardTraversalWorker()
        self.parser = ParserValidatorWorker()
        self.media = MediaMetadataWorker()
        self.ocr = OCRTextImageWorker()

    def enrich_page(self, artifact: PageArtifact) -> PageArtifact:
        axe_results = self.axe.analyze(artifact.html)
        computed_samples = artifact.render_metrics.get("computed_contrast_samples")
        contrast = self.contrast.analyze(artifact.html, computed_samples=computed_samples if isinstance(computed_samples, dict) else None)
        parsing = self.parser.analyze(artifact.html)
        media = self.media.analyze(artifact.html)
        text_images = self.ocr.detect_candidates(artifact.html)

        render_metrics = self.css.analyze(artifact.render_metrics)
        render_metrics.setdefault("contrast_samples", contrast.text_samples)
        render_metrics.setdefault("non_text_contrast_samples", contrast.non_text_samples)
        render_metrics.setdefault("contrast_violations", contrast.violations)
        render_metrics.setdefault("non_text_contrast_violations", contrast.non_text_violations)
        render_metrics.setdefault("ocr_text_image_candidates", text_images)

        interaction_metrics = self.keyboard.analyze(artifact.html, artifact.interaction_metrics)
        interaction_metrics.setdefault("axe_issue_count", len(axe_results["issues"]))

        artifact.render_metrics = render_metrics
        artifact.interaction_metrics = interaction_metrics
        artifact.media_metadata = {**media, **artifact.media_metadata, **parsing, "axe_issues": axe_results["issues"]}
        return artifact
