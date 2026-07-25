# CLAUDE.md — surgical-fhir-pipeline

This file orients both human engineers and AI agents working in this repository.
Read it before making any change. See the linked docs for depth.

---

## What this repo is

A governance-first pipeline that maps synthetic robotic-assisted surgery (RAS)
telemetry to FHIR R4B. The core claim: the 80% exchangeability rate is the
point, not a failure. A pipeline that reports 100% is hiding data loss.

The value lives in three layers:
1. `terminology.py` — every code binding carries a formal trust status
2. `mapping.py` — every lossy decision is logged; cases drop rather than degrade
3. `quality.py` — the governance report is a first-class deliverable

**No PHI. No real patient data. No proprietary schema. Ever.**

---

## Commands

```bash
# Install
pip install -r requirements.txt

# Run all tests (must pass before every commit)
PYTHONPATH=src pytest tests/ -q

# Generate pipeline output (25 cases, deterministic seed)
PYTHONPATH=src python scripts/generate.py -n 25

# Serve the FHIR REST API
PYTHONPATH=src uvicorn surgical_fhir.api:app --reload

# Spot-check the API
curl localhost:8000/metadata
curl localhost:8000/quality-report
curl localhost:8000/Procedure
```

---

## Architecture overview

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full system model.

```
SurgicalCase (source)
  └─▶ terminology.py   [resolve local codes → SNOMED/LOINC/UCUM + trust status]
  └─▶ mapping.py       [transform → FHIR; log every MappingIssue; drop, don't degrade]
  └─▶ quality.py       [build QualityReport from all MappingResults]
  └─▶ store.py         [in-memory FHIR store — NOT a FHIR server]
  └─▶ api.py           [FastAPI; CapabilityStatement; OperationOutcome errors]
```

See [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md) for bounded contexts, aggregates, and invariants.

---

## Engineering principles

These are not aspirational. They are load-bearing — violations break things downstream.

**Drop, don't degrade.** An unmapped code or missing laterality drops the case
entirely. A text-only, uncoded procedure record is worse than absent — it is
invisible to every query that matters while looking like valid data.

**Fail-loud on unknown input.** `UnmappedConceptError` is raised, not swallowed.
Unknown units raise. Unsupported search parameters return 400, not an empty
bundle. Silent degradation is how claims get denied and registry submissions
get rejected six months later.

**Trust must be explicit.** Every terminology binding carries `BindingStatus.VERIFIED`
or `PROVISIONAL`. There is no implicit promotion. A test (`test_procedure_bindings_are_honestly_marked_provisional`)
asserts SNOMED bindings stay provisional permanently — that test must never be
removed or weakened.

**Reports enforce invariants, not prose.** The quality report counts resources by
type. An orphan Procedure was caught this way — not by the docstring that
claimed to prevent it. Tests, not comments, are the enforcement mechanism.

**Reproducibility is a regulated-domain habit.** `seed=42` is the default
everywhere. CI runs `generate.py -n 25` and fails if output differs.

---

## Invariants that must not be reversed without a DECISIONS.md entry

| Invariant | Where enforced |
|---|---|
| Cases with unmapped procedure codes are dropped | `mapping.py:map_procedure`, D1 in DECISIONS.md |
| Missing laterality on a sided procedure drops the case | `mapping.py:map_procedure`, D2 |
| SNOMED procedure bindings stay PROVISIONAL | `terminology.py`, `test_procedure_bindings_are_honestly_marked_provisional` |
| No raw MRN in FHIR output | `mapping.py:_pseudonymise`, `test_no_raw_mrn_leaks_into_output` |
| Out-of-range physio → `entered-in-error`, no value | `mapping.py:map_observations`, `test_implausible_physio_value_is_not_emitted_as_final` |
| Bundle uses PUT, not POST | `mapping.py:to_transaction_bundle`, D3 |
| Search raises on unsupported params | `store.py:search`, D4 |

To reverse any of the above: add a new decision to `DECISIONS.md` first, then
update the corresponding test, then update the implementation.

---

## When to use plan mode

Use `/plan` before:
- Adding a new quality gate (changes the drop/degrade boundary)
- Changing any `BindingStatus` from PROVISIONAL → VERIFIED
- Adding a new FHIR resource type to the output
- Modifying `QualityReport` schema (downstream consumers depend on it)
- Changing `_pseudonymise` — this touches the PHI boundary

Do not use plan mode for: adding terminology bindings (editorial, not
architectural), adding tests, updating documentation.

---

## When to invoke skills

- `/security-review` — before any change touching `_pseudonymise`, PHI-adjacent
  fields, or authentication (when added)
- `/architecture-review` — before adding a new module or changing system boundaries
- `/tdd-loop` — when adding a new quality gate or behavior that needs test-first development
- `/ddd-review` — when introducing concepts not already in `DOMAIN_MODEL.md`

---

## What not to touch without explicit discussion

- `_PHYSIO_BOUNDS` in `mapping.py` — clinical bounds, not implementation choices
- The `BindingStatus` enum and `is_trusted` property — the trust system's foundation
- `test_referential_integrity_no_orphan_resources` — the regression test for a real bug
- `test_no_raw_mrn_leaks_into_output` — the PHI boundary test
- `DECISIONS.md` — append only; never edit existing entries

---

## Pointers

| Topic | Document |
|---|---|
| System boundaries, component model, interfaces | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Domain model, bounded contexts, ubiquitous language | [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md) |
| Every design decision with counter-argument | [`DECISIONS.md`](DECISIONS.md) |
| Test philosophy and coverage gaps | [`TEST_STRATEGY.md`](TEST_STRATEGY.md) *(coming)* |
| Threat model and security posture | [`SECURITY.md`](SECURITY.md) *(coming)* |
| Sample quality report output | [`docs/sample-quality-report.txt`](docs/sample-quality-report.txt) |
