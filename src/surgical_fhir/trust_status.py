"""Trust-status primitives for terminology bindings.

Isolated from the full terminology lookup tables so that downstream consumers
(e.g. a prior-auth-agent) can reason about binding trust without importing the
SNOMED/LOINC dictionaries. Import this module directly when you only need
BindingStatus, Binding, or UnmappedConceptError.

terminology.py re-exports all three for callers that already use that import.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BindingStatus(str, Enum):
    """How much you are allowed to trust this mapping."""

    VERIFIED = "verified"      # checked against an authoritative release
    PROVISIONAL = "provisional"  # asserted by a developer; NOT clinically safe
    DEPRECATED = "deprecated"  # superseded; retained for historical decode


@dataclass(frozen=True)
class Binding:
    """A single local-code -> standard-code mapping with its evidence."""

    local_code: str
    system: str
    code: str
    display: str
    status: BindingStatus
    source: str
    note: str = ""

    @property
    def is_trusted(self) -> bool:
        return self.status is BindingStatus.VERIFIED


class UnmappedConceptError(KeyError):
    """Raised when a local code has no binding. Deliberately loud."""

    def __init__(self, local_code: str, domain: str):
        self.local_code = local_code
        self.domain = domain
        super().__init__(
            f"No {domain} binding for local code {local_code!r}. "
            "Refusing to emit an uncoded CodeableConcept."
        )
