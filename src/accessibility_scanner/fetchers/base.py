from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import PageArtifact, ScanRequest


class BaseFetcher(ABC):
    @abstractmethod
    def setup(self, request: ScanRequest) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def fetch_page(
        self,
        url: str,
        depth: int,
        request: ScanRequest,
        run_id: str,
    ) -> PageArtifact:
        raise NotImplementedError

    @abstractmethod
    def teardown(self) -> None:
        raise NotImplementedError
