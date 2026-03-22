from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class CheckpointStatus(str, Enum):
    PASS = "Pass"
    FAIL = "Fail"
    CANNOT_VERIFY = "Cannot verify automatically"
    NOT_APPLICABLE = "Not applicable"


class PolicyMode(str, Enum):
    STRICT_GOV = "strict_gov"
    AUTOMATION_ONLY = "automation_only"


class Bucket(str, Enum):
    CONTENT_EQUIVALENCE = "content_equivalence"
    LAYOUT_PERCEPTION = "layout_perception"
    INTERACTION_NAVIGATION = "interaction_navigation"
    SEMANTICS_TRANSACTION = "semantics_transaction"


@dataclass
class ScanRequest:
    start_urls: list[str]
    domain_scope: str
    max_depth: int = 2
    max_pages: int = 25
    auth_script_ref: str | None = None
    policy_mode: PolicyMode = PolicyMode.STRICT_GOV
    checkpoint_overrides: dict[str, bool] = field(default_factory=dict)
    evidence_level: str = "standard"


@dataclass
class CrawlTarget:
    url: str
    depth: int


@dataclass
class PageArtifact:
    url: str
    depth: int
    html: str
    title: str
    links: list[str] = field(default_factory=list)
    screenshot_evidence_id: str | None = None
    dom_evidence_id: str | None = None
    logs_evidence_id: str | None = None
    render_metrics: dict[str, Any] = field(default_factory=dict)
    interaction_metrics: dict[str, Any] = field(default_factory=dict)
    media_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckpointResult:
    checkpoint_id: str
    bucket: Bucket
    status: CheckpointStatus
    applicable: bool
    page_url: str
    selector_or_target: str | None
    evidence_refs: list[str] = field(default_factory=list)
    rationale: str = ""
    manual_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bucket"] = self.bucket.value
        data["status"] = self.status.value
        return data


@dataclass
class EvidenceRecord:
    evidence_id: str
    kind: str
    path: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AggregatedCheckpoint:
    checkpoint_id: str
    bucket: Bucket
    status: CheckpointStatus
    applicable: bool
    evidence_refs: list[str]
    pages: list[str]
    rationale: str
    manual_required: bool

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bucket"] = self.bucket.value
        data["status"] = self.status.value
        return data


@dataclass
class ScanReport:
    run_id: str
    request: ScanRequest
    totals: dict[str, int]
    checkpoint_results: list[AggregatedCheckpoint]
    per_page_results: list[CheckpointResult]
    strict_decision: str
    automation_decision: str
    cannot_verify_checkpoints: list[str]
    remediation_summary: list[dict[str, Any]]
    artifacts_dir: str
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "request": {
                **asdict(self.request),
                "policy_mode": self.request.policy_mode.value,
            },
            "totals": self.totals,
            "checkpoint_results": [r.to_dict() for r in self.checkpoint_results],
            "per_page_results": [r.to_dict() for r in self.per_page_results],
            "strict_decision": self.strict_decision,
            "automation_decision": self.automation_decision,
            "cannot_verify_checkpoints": self.cannot_verify_checkpoints,
            "remediation_summary": self.remediation_summary,
            "artifacts_dir": self.artifacts_dir,
            "errors": self.errors,
        }
