from __future__ import annotations

import json
from pathlib import Path

from .models import ScanReport


def write_report(report: ScanReport, artifacts_dir: str) -> str:
    path = Path(artifacts_dir) / "scan-report.json"
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return str(path)
