"""FHIR R4B Provenance resource generation for mapped cases.

One Provenance per mapped case. Records:
  - The pipeline as agent (assembler, identified by repo + version)
  - The OR Platform source record as origin entity
  - Each distinct terminology binding used (procedure code + observation metrics)
    as a source entity, with machine-readable trust status encoded via a local
    extension URL on entity.what

Trust status is also encoded in entity.what.display as a human-readable fallback
("trust:provisional" / "trust:verified") in case the extension is not parsed by
the consumer.

Extension URL: urn:local:surgical-fhir:binding-trust-status
Extension value type: valueCode ("provisional" | "verified" | "deprecated")

Scope: this module only generates Provenance. Loading into the store and
serving via $everything are api.py concerns.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fhir.resources.R4B.provenance import Provenance

from . import terminology as tx
from .source_schema import SurgicalCase

_TRUST_EXT_URL = "urn:local:surgical-fhir:binding-trust-status"
_PIPELINE_VERSION = "0.1.0"
_PIPELINE_REPO = "https://github.com/smahanti8/surgical-fhir-pipeline"


def map_provenance(case: SurgicalCase, targets: list[Any]) -> Provenance:
    """Build a Provenance resource for one successfully mapped OR case.

    targets must be the FHIR resources already produced for this case —
    Encounter, Patient, Procedure, Device(s), Observation(s). The caller
    is responsible for passing a non-empty list; dropped cases have no
    Provenance.

    Observation bindings are deduplicated by metric type: if a case has
    forty Heart-rate Observations, the Provenance records the LOINC 8867-4
    binding once, not forty times.
    """
    proc_binding = tx.procedure_binding(case.procedure_local_code)
    distinct_metrics = sorted({s.metric for s in case.physio})
    obs_bindings = [tx.observation_binding(m) for m in distinct_metrics]

    return Provenance(
        id=f"prov-{case.case_id}",
        target=[{"reference": f"{r.__resource_type__}/{r.id}"} for r in targets],
        recorded=datetime.now(timezone.utc).isoformat(),
        activity={
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-DataOperation",
                    "code": "TRANS",
                    "display": "Transform",
                }
            ]
        },
        agent=[
            {
                "type": {
                    "coding": [
                        {
                            "system": (
                                "http://terminology.hl7.org/CodeSystem/"
                                "provenance-participant-type"
                            ),
                            "code": "assembler",
                            "display": "Assembler",
                        }
                    ]
                },
                "who": {
                    "display": f"surgical-fhir-pipeline v{_PIPELINE_VERSION}",
                    "identifier": {
                        "system": _PIPELINE_REPO,
                        "value": _PIPELINE_VERSION,
                    },
                },
            }
        ],
        entity=_build_entities(case, proc_binding, obs_bindings),
    )


def _build_entities(
    case: SurgicalCase,
    proc_binding: tx.Binding,
    obs_bindings: list[tx.Binding],
) -> list[dict]:
    entities: list[dict] = [
        {
            "role": "source",
            "what": {
                "display": f"OR Platform source record {case.case_id}",
                "identifier": {
                    "system": "urn:local:or-platform:case",
                    "value": case.case_id,
                },
            },
        }
    ]

    for binding in [proc_binding, *obs_bindings]:
        entities.append(
            {
                "role": "source",
                "what": {
                    "display": (
                        f"{binding.display} | {binding.system}:{binding.code}"
                        f" | trust:{binding.status.value}"
                    ),
                    "identifier": {
                        "system": binding.system,
                        "value": binding.code,
                    },
                    "extension": [
                        {
                            "url": _TRUST_EXT_URL,
                            "valueCode": binding.status.value,
                        }
                    ],
                },
            }
        )

    return entities
