# Domain Model — Surgical RAS → FHIR R4B Pipeline

This document makes the domain design explicit. The code already knows this —
reading it is how the model was recovered. When new features are added, check
that the concepts below stay internally consistent.

---

## 1. Ubiquitous language

These terms have precise meaning in this codebase. Use them consistently.

| Term | Meaning | Defined in |
|---|---|---|
| **SurgicalCase** | One robotic-assisted surgical episode from the OR platform | `source_schema.py` |
| **Local code** | Procedure or observation code in the OR platform's proprietary vocabulary | `source_schema.py`, `terminology.py` |
| **Binding** | A mapping from a local code to a standard terminology (SNOMED, LOINC, UCUM) | `terminology.py` |
| **BindingStatus** | Formal trust level of a binding: `VERIFIED` or `PROVISIONAL` | `terminology.py` |
| **VERIFIED binding** | Clinically validated and authorised under a terminology licence | `terminology.py` |
| **PROVISIONAL binding** | Pending validation — structurally correct, clinically unconfirmed | `terminology.py` |
| **UnmappedConceptError** | A local code exists in the source but has no binding at all | `terminology.py` |
| **MappingIssue** | Audit record for one lossy or uncertain transformation decision | `mapping.py` |
| **MappingResult** | Outcome of mapping one SurgicalCase: resources + issues + dropped flag | `mapping.py` |
| **dropped** | A SurgicalCase for which no FHIR output is produced (clean absence) | `mapping.py` |
| **exchangeable** | A SurgicalCase that produced a valid FHIR bundle, even with warnings | `mapping.py`, `quality.py` |
| **entered-in-error** | A PhysioSample observation with an out-of-range value — value removed | `mapping.py` |
| **QualityReport** | Governance artefact summarising pipeline outcomes across all cases | `quality.py` |
| **Transaction bundle** | FHIR R4B Bundle with PUT entries — the unit of exchange | `mapping.py` |
| **Idempotent PUT** | A bundle that can be submitted multiple times with the same result | `mapping.py` D3 |
| **_pseudonymise** | SHA-256 of MRN — reduces re-identification risk, NOT de-identification | `mapping.py` |
| **Telemetry downsample** | Reduction of 1 Hz × 4 h (72,000) → ~40 Observations per case | `mapping.py` |
| **Laterality** | Left/right designation for a sided procedure (knee, shoulder, hip) | `source_schema.py`, `mapping.py` |
| **Trust escalation** | A binding status can move from PROVISIONAL → VERIFIED but never in reverse | `terminology.py` |
| **Fail-loud** | The system raises an exception rather than producing silent degraded output | `terminology.py`, `store.py`, `api.py` |

---

## 2. Bounded contexts

```
┌──────────────────────────────────────────────────────────┐
│  Source Context                                          │
│  "The OR platform's world"                               │
│                                                          │
│  SurgicalCase, DeviceRecord, TelemetryEvent, PhysioSample│
│  Local procedure codes, local units, raw MRN             │
│  source_schema.py  ·  generator.py                       │
└──────────────────────────┬───────────────────────────────┘
                           │ Translation layer
                           │ (terminology.py)
┌──────────────────────────▼───────────────────────────────┐
│  Interoperability Context                                │
│  "The mapping and governance layer"                      │
│                                                          │
│  Binding, BindingStatus, MappingIssue, MappingResult     │
│  QualityReport                                           │
│  terminology.py  ·  mapping.py  ·  quality.py            │
└──────────────────────────┬───────────────────────────────┘
                           │ FHIR R4B resources
                           │ (to_transaction_bundle)
┌──────────────────────────▼───────────────────────────────┐
│  Clinical Exchange Context                               │
│  "The FHIR world — standard language"                    │
│                                                          │
│  Patient, Encounter, Procedure, Device, Observation      │
│  CapabilityStatement, OperationOutcome, searchset Bundle │
│  store.py  ·  api.py                                     │
└──────────────────────────────────────────────────────────┘
```

**Context boundaries:**
- `source_schema.py` lives entirely in the Source Context. It never imports from `mapping.py` or `terminology.py`.
- `terminology.py` is a translation service — it knows both local codes and standard codes, but returns standard-code `Binding` objects.
- `mapping.py` consumes the Source Context and produces the Clinical Exchange Context. It never passes raw local codes downstream.
- `store.py` and `api.py` live in the Clinical Exchange Context only — they handle FHIR dicts, not `SurgicalCase` objects.

---

## 3. Aggregates and invariants

### SurgicalCase aggregate (Source Context)

Root: `SurgicalCase`
Contains: `DeviceRecord[]`, `TelemetryEvent[]`, `PhysioSample[]`

**Invariants:**
- A SurgicalCase either maps completely or drops completely — partial output is forbidden
- `patient_mrn` must not survive into any FHIR resource in its raw form
- `laterality` is required for sided procedures (knee, shoulder, hip, wrist)

### MappingResult aggregate (Interoperability Context)

Root: `MappingResult`
Contains: `list[dict]` (FHIR resource dicts), `list[MappingIssue]`

**Invariants:**
- `dropped == True` ↔ `resources == []` — these two must always agree
- `issues` is never empty when `dropped == True` — every drop has a reason
- Issue severity is monotonically escalating: INFO → WARN → ERROR — a case can gain severity but not lose it during a single mapping run
- The referential integrity invariant: every Procedure in `resources` has a corresponding Patient and Encounter

### QualityReport aggregate (Interoperability Context)

Root: `QualityReport`

**Invariants:**
- `total_cases == exchangeable_count + dropped_count`
- `resource_counts` sums only resources from non-dropped cases
- `binding_trust` counts are computed from the active binding map, not from per-case results — so they reflect the current state of `terminology.py`

---

## 4. Domain services

| Service | Module | What it does |
|---|---|---|
| **Terminology resolver** | `terminology.py` | Translates local codes → standard codes + trust status. Raises on unknowns. |
| **Case mapper** | `mapping.py:map_case` | Orchestrates one SurgicalCase → MappingResult. Enforces all quality gates. |
| **Bundle builder** | `mapping.py:to_transaction_bundle` | Produces an idempotent PUT bundle from a successful MappingResult. |
| **Quality reporter** | `quality.py:build_report` | Aggregates all MappingResults into a QualityReport. |

---

## 5. Quality gates (domain policies)

These are business rules, not implementation choices:

```
IF procedure_code not in terminology_map:
    RAISE UnmappedConceptError
    DROP case
    RECORD MappingIssue(severity=ERROR, reason="unmapped_procedure_code")

IF procedure requires laterality AND laterality is missing:
    DROP case
    RECORD MappingIssue(severity=ERROR, reason="missing_laterality")
    # Rationale: wrong-site surgery class defect — worse than no record

IF physio_value outside _PHYSIO_BOUNDS[loinc_code]:
    EMIT Observation(status="entered-in-error", value=absent)
    RECORD MappingIssue(severity=WARN, reason="out_of_range_physio")
    # Rationale: preserve the failure evidence; remove the misleading value

IF telemetry_count > threshold:
    DOWNSAMPLE to ~40 representative events
    RECORD MappingIssue(severity=INFO, reason="telemetry_downsampled")
```

---

## 6. Domain events (implicit — made explicit)

These state transitions occur during a pipeline run. They are currently implicit in
`mapping.py` and surfaced only via `MappingIssue`. Future event sourcing could
make them explicit:

| Event | Trigger | Current representation |
|---|---|---|
| `ProcedureCodeUnmapped` | `look_up_procedure` raises `UnmappedConceptError` | `MappingIssue(severity=ERROR, ...)` |
| `LateralityMissing` | Sided procedure with no laterality field | `MappingIssue(severity=ERROR, ...)` |
| `CaseDropped` | Any ERROR-level issue | `MappingResult.dropped = True` |
| `PhysioOutOfRange` | Value outside `_PHYSIO_BOUNDS` | `MappingIssue(severity=WARN, ...)` + `entered-in-error` Observation |
| `TelemetryDownsampled` | Event count exceeds threshold | `MappingIssue(severity=INFO, ...)` |
| `BindingProvisional` | Terminology lookup returns `BindingStatus.PROVISIONAL` | Returned in Binding tuple; surfaced in QualityReport |
| `CaseExchangeable` | Mapping completes with only INFO/WARN issues | `MappingResult.dropped = False` |

---

## 7. What the 80% means (domain interpretation)

The 20/25 exchangeable rate is not a failure metric. It is the pipeline's
honest answer to the question: *"How much of this vendor data can we put into
a FHIR store that a downstream system could query correctly?"*

The 5 dropped cases are **cleaner than 5 degraded cases would be**. A text-only,
uncoded Procedure record answers zero queries from a registry that searches by
SNOMED code. It is invisible while appearing complete.

The quality report is the governance artefact that proves the pipeline is being
honest about its own output — it does not hide the drops, it counts them.
