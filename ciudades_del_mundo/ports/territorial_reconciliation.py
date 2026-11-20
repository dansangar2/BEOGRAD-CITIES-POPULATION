"""Controlled boundaries for the alternative territorial strategy."""
from typing import Callable, Protocol

from ciudades_del_mundo.domain.territorial_reconciliation import TerritorialReference


class TerritorialReferenceReader(Protocol):
    def reference(self, country: str) -> dict:
        """Return a reviewed SQL reference, never a scraping recipe."""


class TerritorialEvidenceReader(Protocol):
    def discover(self, reference: TerritorialReference, *, checkpoint: Callable[[], None]) -> dict:
        """Bounded discovery; URLs and parsing remain application controlled."""


class TerritorialProposalProvider(Protocol):
    model: str

    def complete_json(self, *, system_prompt: str, payload: dict, image_url: str = "") -> dict:
        """Propose JSON or request a named, read-only bounded lookup."""


class TerritorialRunStore(Protocol):
    def apply(self, run_id: str, *, checkpoint: Callable[[], None], expected_plan_hash: str | None = None) -> dict:
        """Revalidate inputs and commit once; never delete missing source rows."""
