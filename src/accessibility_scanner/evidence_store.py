from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import EvidenceRecord


class EvidenceStore:
    def __init__(self, root: str = "artifacts") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._counter = 0
        self._index: dict[str, EvidenceRecord] = {}

    def run_dir(self, run_id: str) -> Path:
        path = self.root / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def add_text(self, run_id: str, name: str, value: str, metadata: dict[str, Any] | None = None) -> str:
        run_dir = self.run_dir(run_id)
        file_path = run_dir / f"{name}.txt"
        file_path.write_text(value, encoding="utf-8")
        return self._register("text", str(file_path), metadata or {})

    def add_json(self, run_id: str, name: str, value: Any, metadata: dict[str, Any] | None = None) -> str:
        run_dir = self.run_dir(run_id)
        file_path = run_dir / f"{name}.json"
        file_path.write_text(json.dumps(value, indent=2), encoding="utf-8")
        return self._register("json", str(file_path), metadata or {})

    def add_file_ref(self, kind: str, file_path: str, metadata: dict[str, Any] | None = None) -> str:
        return self._register(kind, file_path, metadata or {})

    def _register(self, kind: str, path: str, metadata: dict[str, Any]) -> str:
        self._counter += 1
        evidence_id = f"ev-{self._counter:06d}"
        self._index[evidence_id] = EvidenceRecord(
            evidence_id=evidence_id,
            kind=kind,
            path=path,
            metadata=metadata,
        )
        return evidence_id

    def dump_index(self, run_id: str) -> str:
        run_dir = self.run_dir(run_id)
        path = run_dir / "evidence-index.json"
        payload = {evidence_id: asdict(record) for evidence_id, record in self._index.items()}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(path)

    @property
    def index(self) -> dict[str, dict[str, Any]]:
        return {key: asdict(value) for key, value in self._index.items()}
