# Architecture — Surgical RAS → FHIR R4B Pipeline

This document describes the system as a whole — its external boundaries, internal
components, data flows, interface contracts, and the qualities it is designed to
guarantee. Implementation detail lives in the source code and in `DECISIONS.md`.

---

## 1. System context

```
┌────────────────────────────────────────────────────────────────────────┐
│  OR Platform (vendor-shaped — not controlled)                          │
│  SurgicalCase · DeviceRecord · TelemetryEvent · PhysioSample           │
│  Local codes · MRN · laterality flag · high-frequency sensor data      │
└────────────────────┬───────────────────────────────────────────────────┘
                     │ synthetic in this repo (generator.py)
                     │ real in production (OR platform export API)
                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│  Interoperability Pipeline  (this system)                              │
│                                                                        │
│  Terminology resolution → FHIR mapping → quality governance            │
│  + in-memory FHIR store + read-only REST API                           │
└───────┬──────────────────────────────────────────────┬─────────────────┘
        │ FHIR R4B Transaction Bundles                 │ QualityReport
        │ (Procedure, Patient, Encounter, Device,      │ (exchangeable %, 
        │  Observation, OperationOutcome)              │  MappingIssues,
        ▼                                              │  resource counts)
┌─────────────────┐                                   ▼
│ FHIR Consumers  │                       ┌─────────────────────────┐
│ EHR / Registry  │                       │ Governance & Audit      │
│ (not in scope)  │                       │ consumers               │
└─────────────────┘                       └─────────────────────────┘
```

**Actors:**

| Actor | Role | In scope? |
|---|---|---|
| OR Platform | Produces raw surgical data in a vendor schema | Simulated (`generator.py`) |
| Interoperability Pipeline | Transforms, governs, stores, and serves FHIR data | **Yes — full scope** |
| FHIR Consumer (EHR, registry) | Receives and queries FHIR resources | No — API tested, consumer not built |
| Governance auditor | Reviews QualityReport, MappingIssues | Partially — report produced, UI in `docs/` |

---

## 2. Component model

```
src/surgical_fhir/
│
├── source_schema.py    [Source Boundary]
│   Pure data container — the vendor's world view.
│   SurgicalCase, DeviceRecord, TelemetryEvent, PhysioSample.
│   No FHIR. No domain logic. Stable: never changes unless the OR platform does.
│
├── generator.py        [Synthetic Harness — not a pipeline component]
│   Produces calibrated defective cases (seed=42) for deterministic testing.
│   ~8% unmapped codes, ~4% missing laterality, ~6% out-of-range physio.
│   ONLY used in tests and scripts — never imported by pipeline modules.
│
├── terminology.py      [Terminology Service]
│   ╔═══════════════════════════════════════════════════════════╗
│   ║  THE MOST CRITICAL MODULE. Every binding has a trust.    ║
│   ║  VERIFIED = clinically validated. PROVISIONAL = not yet. ║
│   ╚═══════════════════════════════════════════════════════════╝
│   Raises UnmappedConceptError for unknown local codes (fail-loud).
│   Returns (Binding, BindingStatus) — callers cannot ignore the status.
│
├── mapping.py          [Mapper — the pipeline core]
│   Source → FHIR R4B. Every lossy decision produces a MappingIssue.
│   Guarantees: no raw MRN survives (_pseudonymise). Cases with unmapped
│   procedure codes or missing laterality are dropped in full. Out-of-range
│   physio becomes entered-in-error with value removed.
│   Output: MappingResult per case (resources + issues), to_transaction_bundle.
│
├── quality.py          [Governance Layer]
│   Consumes all MappingResults → QualityReport.
│   First-class deliverable, not a byproduct.
│   Reports: total cases, exchangeable, dropped, resource counts by type,
│   binding trust breakdown, sample MappingIssues.
│
├── store.py            [In-Memory FHIR Store]
│   ╔═══════════════════════════════════════════════╗
│   ║  Scope honesty: NOT a FHIR server.            ║
│   ║  In-memory. No persistence. No auth.          ║
│   ╚═══════════════════════════════════════════════╝
│   Raises ValueError on unsupported search parameters (fail-loud).
│   Validates resource type against whitelist.
│
└── api.py              [FHIR REST Surface]
    FastAPI. Read-only: GET /{resource_type}/{id}, GET /{resource_type} (search).
    GET /metadata → CapabilityStatement.
    GET /quality-report → QualityReport.
    Returns OperationOutcome on errors.
    No auth. No write endpoints. No conditional operations.
```

---

## 3. Data flow

```
generate_cases(n=25, seed=42)
        │
        ▼
[SurgicalCase list]
        │
        ├──▶ look_up_procedure(local_code)
        │         terminology.py
        │         → (snomed_code, BindingStatus) OR raises UnmappedConceptError
        │
        ├──▶ map_case(surgical_case, terminology)
        │         mapping.py
        │         Checks laterality
        │         Pseudonymises MRN
        │         Maps physio samples → check bounds → Observation (or entered-in-error)
        │         Downsamples telemetry
        │         → MappingResult{resources: [...], issues: [MappingIssue, ...]}
        │         OR MappingResult{dropped: True, issues: [...]}
        │
        ├──▶ to_transaction_bundle(mapping_result)
        │         PUT entries only — idempotent
        │         → Bundle (FHIR R4B)
        │
        ├──▶ store.put_bundle(bundle)
        │         Indexes by resource type and id
        │
        └──▶ build_report(all_mapping_results)
                  quality.py
                  → QualityReport (JSON serialisable)
```

---

## 4. Interface contracts

### 4.1 `terminology.py` — public API

```python
look_up_procedure(local_code: str) -> tuple[Binding, BindingStatus]
    # raises UnmappedConceptError if local_code not in _PROCEDURE_MAP
    # never returns (None, ...)

look_up_observation(loinc_code: str) -> tuple[Binding, BindingStatus]
    # raises UnmappedConceptError if loinc_code not in _OBSERVATION_MAP

look_up_unit(unit_str: str) -> str  # UCUM code
    # raises ValueError if unit_str not in _UNIT_MAP
```

### 4.2 `mapping.py` — public API

```python
map_case(case: SurgicalCase) -> MappingResult
    # MappingResult.dropped: bool — True if case was dropped
    # MappingResult.resources: list[dict] — FHIR JSON dicts, empty if dropped
    # MappingResult.issues: list[MappingIssue] — audit trail

to_transaction_bundle(result: MappingResult) -> dict
    # Returns FHIR R4B Bundle JSON
    # All entries use PUT (method: "PUT") — idempotent by resource id
    # Precondition: result.dropped == False
```

### 4.3 `api.py` — REST API

| Method | Path | Returns | Status codes |
|---|---|---|---|
| GET | `/metadata` | CapabilityStatement | 200 |
| GET | `/{resource_type}` | searchset Bundle | 200, 400 (unsupported param) |
| GET | `/{resource_type}/{id}` | resource or OperationOutcome | 200, 404 |
| GET | `/quality-report` | QualityReport (JSON) | 200 |

Supported resource types: `Procedure`, `Observation`, `Patient`, `Encounter`, `Device`

Supported search parameters: `patient` (on Observation), `code` (on Observation)

All other search parameters → 400 OperationOutcome (by design — see DECISIONS.md D4)

---

## 5. Quality attributes (Non-Functional Requirements)

| NFR | Guarantee | Where enforced |
|---|---|---|
| **Reproducibility** | Same inputs → same outputs. `seed=42` default everywhere. | CI: `generate.py -n 25` + diff check |
| **Idempotency** | Re-running the pipeline against a FHIR store produces identical state. | PUT bundles; UUIDs derived from MRN + case ID |
| **Fail-loud** | Unknown codes, unsupported params, missing required fields raise immediately. | `terminology.py`, `store.py`, `api.py` |
| **Drop, don't degrade** | No partially-mapped records in output. A case is either complete or absent. | `mapping.py:map_case` |
| **No PHI** | No raw MRN or patient identifier in any FHIR resource. | `mapping.py:_pseudonymise`; `test_no_raw_mrn_leaks_into_output` |
| **Honest trust** | Every terminology binding carries its trust status. No silent promotion. | `terminology.py`; `test_procedure_bindings_are_honestly_marked_provisional` |

---

## 6. Explicit limitations (scope honesty)

These are design decisions, not gaps:

- **No persistence.** Store is in-memory. Restart clears all resources. HAPI FHIR or PostgreSQL would be the next step.
- **No auth.** API is unauthenticated. No tokens, no SMART on FHIR. Required before any production use.
- **No write API.** Clients cannot PUT, POST, or PATCH resources via the REST surface.
- **No validated terminology server.** SNOMED bindings are provisional — not verified against a Snowstorm instance with a licence.
- **No FHIR validator in CI.** Resources are hand-structured — not validated against a profile or the official HL7 validator.
- **No telemetry persistence.** `TelemetryEvent` is downsampled to ~40 per case. The raw signal is discarded.
- **_pseudonymise is NOT de-identification.** SHA-256 of MRN is still linkable to the original. Not compliant with HIPAA Safe Harbor or GDPR pseudonymisation for real data.

---

## 7. Next architectural moves (not implemented)

These are documented so reviewers understand the intended trajectory:

1. **Validate bundles against US Core or a surgical IG** using the HL7 FHIR validator
2. **Replace in-memory store with HAPI FHIR** to prove the bundles load into a real server
3. **Connect to Snowstorm** to verify and promote PROVISIONAL → VERIFIED bindings
4. **Add SMART on FHIR auth** before any consumer-facing deployment
5. **Separate telemetry to a TSDB** — keep FHIR for exchange, use InfluxDB/TimescaleDB for sensor data
