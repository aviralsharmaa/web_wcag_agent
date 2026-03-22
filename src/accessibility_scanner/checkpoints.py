from __future__ import annotations

from dataclasses import dataclass

from .models import Bucket


@dataclass(frozen=True)
class CheckpointMeta:
    checkpoint_id: str
    bucket: Bucket
    title: str
    manual_component: bool


CHECKPOINTS: list[CheckpointMeta] = [
    # ── Content Equivalence ───────────────────────────────────────────
    CheckpointMeta("1.1.1", Bucket.CONTENT_EQUIVALENCE, "Non-text Content", True),
    CheckpointMeta("1.2.1", Bucket.CONTENT_EQUIVALENCE, "Audio-only and Video-only (Prerecorded)", True),
    CheckpointMeta("1.2.2", Bucket.CONTENT_EQUIVALENCE, "Captions (Prerecorded)", True),
    CheckpointMeta("1.2.3", Bucket.CONTENT_EQUIVALENCE, "Audio Description or Media Alternative", True),
    CheckpointMeta("1.2.4", Bucket.CONTENT_EQUIVALENCE, "Captions (Live)", True),
    CheckpointMeta("1.2.5", Bucket.CONTENT_EQUIVALENCE, "Audio Description (Prerecorded)", True),
    CheckpointMeta("1.4.5", Bucket.CONTENT_EQUIVALENCE, "Images of Text", True),

    # ── Layout & Perception ───────────────────────────────────────────
    CheckpointMeta("1.3.1", Bucket.LAYOUT_PERCEPTION, "Info and Relationships", True),
    CheckpointMeta("1.3.2", Bucket.LAYOUT_PERCEPTION, "Meaningful Sequence", True),
    CheckpointMeta("1.3.3", Bucket.LAYOUT_PERCEPTION, "Sensory Characteristics", True),
    CheckpointMeta("1.3.4", Bucket.LAYOUT_PERCEPTION, "Orientation", False),
    CheckpointMeta("1.3.5", Bucket.LAYOUT_PERCEPTION, "Identify Input Purpose", False),
    CheckpointMeta("1.4.1", Bucket.LAYOUT_PERCEPTION, "Use of Color", True),
    CheckpointMeta("1.4.2", Bucket.LAYOUT_PERCEPTION, "Audio Control", False),
    CheckpointMeta("1.4.3", Bucket.LAYOUT_PERCEPTION, "Contrast (Minimum)", False),
    CheckpointMeta("1.4.4", Bucket.LAYOUT_PERCEPTION, "Resize text", False),
    CheckpointMeta("1.4.10", Bucket.LAYOUT_PERCEPTION, "Reflow", False),
    CheckpointMeta("1.4.11", Bucket.LAYOUT_PERCEPTION, "Non-text Contrast", False),
    CheckpointMeta("1.4.12", Bucket.LAYOUT_PERCEPTION, "Text Spacing", False),
    CheckpointMeta("1.4.13", Bucket.LAYOUT_PERCEPTION, "Content on Hover or Focus", False),
    CheckpointMeta("2.5.8", Bucket.LAYOUT_PERCEPTION, "Target Size (Minimum)", False),  # NEW

    # ── Interaction & Navigation ──────────────────────────────────────
    CheckpointMeta("2.1.1", Bucket.INTERACTION_NAVIGATION, "Keyboard", True),
    CheckpointMeta("2.1.2", Bucket.INTERACTION_NAVIGATION, "No Keyboard Trap", True),
    CheckpointMeta("2.1.4", Bucket.INTERACTION_NAVIGATION, "Character Key Shortcuts", False),
    CheckpointMeta("2.2.1", Bucket.INTERACTION_NAVIGATION, "Timing Adjustable", True),  # NEW
    CheckpointMeta("2.2.2", Bucket.INTERACTION_NAVIGATION, "Pause, Stop, Hide", True),  # NEW
    CheckpointMeta("2.3.1", Bucket.INTERACTION_NAVIGATION, "Three Flashes or Below Threshold", True),  # NEW
    CheckpointMeta("2.4.1", Bucket.INTERACTION_NAVIGATION, "Bypass Blocks", False),
    CheckpointMeta("2.4.3", Bucket.INTERACTION_NAVIGATION, "Focus Order", True),  # NEW
    CheckpointMeta("2.4.5", Bucket.INTERACTION_NAVIGATION, "Multiple Ways", True),  # NEW
    CheckpointMeta("2.4.7", Bucket.INTERACTION_NAVIGATION, "Focus Visible", True),
    CheckpointMeta("2.4.11", Bucket.INTERACTION_NAVIGATION, "Focus Not Obscured (Minimum)", True),  # NEW
    CheckpointMeta("2.5.1", Bucket.INTERACTION_NAVIGATION, "Pointer Gestures", True),  # NEW
    CheckpointMeta("2.5.2", Bucket.INTERACTION_NAVIGATION, "Pointer Cancellation", True),  # NEW
    CheckpointMeta("2.5.4", Bucket.INTERACTION_NAVIGATION, "Motion Actuation", True),  # NEW
    CheckpointMeta("2.5.7", Bucket.INTERACTION_NAVIGATION, "Dragging Movements", True),  # NEW
    CheckpointMeta("3.2.1", Bucket.INTERACTION_NAVIGATION, "On Focus", True),
    CheckpointMeta("3.2.2", Bucket.INTERACTION_NAVIGATION, "On Input", True),  # NEW
    CheckpointMeta("3.2.3", Bucket.INTERACTION_NAVIGATION, "Consistent Navigation", True),
    CheckpointMeta("3.2.4", Bucket.INTERACTION_NAVIGATION, "Consistent Identification", True),  # NEW
    CheckpointMeta("3.2.6", Bucket.INTERACTION_NAVIGATION, "Consistent Help", True),  # NEW

    # ── Semantics & Transaction ───────────────────────────────────────
    CheckpointMeta("2.4.2", Bucket.SEMANTICS_TRANSACTION, "Page Titled", False),  # NEW
    CheckpointMeta("2.4.4", Bucket.SEMANTICS_TRANSACTION, "Link Purpose (In Context)", True),  # NEW
    CheckpointMeta("2.4.6", Bucket.SEMANTICS_TRANSACTION, "Headings and Labels", True),
    CheckpointMeta("2.5.3", Bucket.SEMANTICS_TRANSACTION, "Label in Name", False),  # NEW
    CheckpointMeta("3.1.1", Bucket.SEMANTICS_TRANSACTION, "Language of Page", False),  # NEW
    CheckpointMeta("3.1.2", Bucket.SEMANTICS_TRANSACTION, "Language of Parts", True),
    CheckpointMeta("3.3.1", Bucket.SEMANTICS_TRANSACTION, "Error Identification", True),
    CheckpointMeta("3.3.2", Bucket.SEMANTICS_TRANSACTION, "Labels or Instructions", False),  # NEW
    CheckpointMeta("3.3.3", Bucket.SEMANTICS_TRANSACTION, "Error Suggestion", True),  # NEW
    CheckpointMeta("3.3.4", Bucket.SEMANTICS_TRANSACTION, "Error Prevention (Legal, Financial, Data)", True),
    CheckpointMeta("3.3.7", Bucket.SEMANTICS_TRANSACTION, "Redundant Entry", True),  # NEW
    CheckpointMeta("3.3.8", Bucket.SEMANTICS_TRANSACTION, "Accessible Authentication (Minimum)", True),  # NEW
    CheckpointMeta("4.1.1", Bucket.SEMANTICS_TRANSACTION, "Parsing", False),
    CheckpointMeta("4.1.2", Bucket.SEMANTICS_TRANSACTION, "Name, Role, Value", False),
    CheckpointMeta("4.1.3", Bucket.SEMANTICS_TRANSACTION, "Status Messages", True),
]

CHECKPOINT_MAP = {item.checkpoint_id: item for item in CHECKPOINTS}

BUCKET_TO_CHECKPOINTS: dict[Bucket, list[str]] = {
    Bucket.CONTENT_EQUIVALENCE: [
        "1.1.1",
        "1.2.1",
        "1.2.2",
        "1.2.3",
        "1.2.4",
        "1.2.5",
        "1.4.5",
    ],
    Bucket.LAYOUT_PERCEPTION: [
        "1.3.1",
        "1.3.2",
        "1.3.3",
        "1.3.4",
        "1.3.5",
        "1.4.1",
        "1.4.2",
        "1.4.3",
        "1.4.4",
        "1.4.10",
        "1.4.11",
        "1.4.12",
        "1.4.13",
        "2.5.8",
    ],
    Bucket.INTERACTION_NAVIGATION: [
        "2.1.1", "2.1.2", "2.1.4",
        "2.2.1", "2.2.2", "2.3.1",
        "2.4.1", "2.4.3", "2.4.5", "2.4.7", "2.4.11",
        "2.5.1", "2.5.2", "2.5.4", "2.5.7",
        "3.2.1", "3.2.2", "3.2.3", "3.2.4", "3.2.6",
    ],
    Bucket.SEMANTICS_TRANSACTION: [
        "2.4.2", "2.4.4", "2.4.6", "2.5.3",
        "3.1.1", "3.1.2",
        "3.3.1", "3.3.2", "3.3.3", "3.3.4", "3.3.7", "3.3.8",
        "4.1.1", "4.1.2", "4.1.3",
    ],
}
