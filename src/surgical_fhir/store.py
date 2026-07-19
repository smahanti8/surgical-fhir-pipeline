"""In-memory FHIR resource store.

Scope honesty: this is NOT a FHIR server. It implements enough of the RESTful
API (read, type-level search on a handful of parameters, capability statement)
to prove the mapping produces genuinely exchangeable resources. A real
deployment puts HAPI FHIR, Firely, or Medplum here and this repo's value is the
mapping + governance layer in front of it. Rewriting a FHIR server as a portfolio
piece would be the wrong instinct — knowing what NOT to build is part of the
skill being demonstrated.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class FHIRStore:
    def __init__(self) -> None:
        self._by_type: dict[str, dict[str, Any]] = defaultdict(dict)

    def load(self, resources: list[Any]) -> None:
        for r in resources:
            self._by_type[r.__resource_type__][r.id] = r

    def read(self, resource_type: str, rid: str) -> Any | None:
        return self._by_type.get(resource_type, {}).get(rid)

    def types(self) -> list[str]:
        return sorted(self._by_type.keys())

    def count(self, resource_type: str) -> int:
        return len(self._by_type.get(resource_type, {}))

    def search(self, resource_type: str, params: dict[str, str]) -> list[Any]:
        """Minimal search. Supports: patient/subject, encounter, code, status.

        Unsupported parameters raise, per the FHIR spec's guidance that a server
        SHOULD signal rather than silently ignore parameters it does not handle.
        Silently ignoring a filter is how a client ends up displaying another
        patient's data.
        """
        supported = {"subject", "patient", "encounter", "code", "status", "_count"}
        unsupported = set(params) - supported
        if unsupported:
            raise ValueError(
                f"Unsupported search parameters: {sorted(unsupported)}. "
                "Refusing to silently ignore a filter."
            )

        results = list(self._by_type.get(resource_type, {}).values())

        def ref_matches(resource: Any, field: str, wanted: str) -> bool:
            val = getattr(resource, field, None)
            if val is None or val.reference is None:
                return False
            return val.reference.split("/")[-1] == wanted.split("/")[-1]

        if "patient" in params or "subject" in params:
            wanted = params.get("patient") or params["subject"]
            results = [r for r in results if ref_matches(r, "subject", wanted)]
        if "encounter" in params:
            results = [
                r for r in results if ref_matches(r, "encounter", params["encounter"])
            ]
        if "status" in params:
            results = [r for r in results if getattr(r, "status", None) == params["status"]]
        if "code" in params:
            wanted = params["code"].split("|")[-1]

            def has_code(r: Any) -> bool:
                code = getattr(r, "code", None)
                if code is None or not code.coding:
                    return False
                return any(c.code == wanted for c in code.coding)

            results = [r for r in results if has_code(r)]

        if "_count" in params:
            results = results[: int(params["_count"])]
        return results
