# surgical-fhir-pipeline

**Maps synthetic robotic-assisted surgery (RAS) telemetry to FHIR R4B and exposes it over a conformant REST API — and reports honestly on everything that breaks along the way.**

```
Cases ingested        : 25
Cases exchangeable    : 20 (80.0%)
Cases dropped         : 5
FHIR resources out    : 953

Terminology binding trust
  verified   : 5/11
  provisional: 6/11   <-- NOT clinically safe without terminology-server validation
```

That 80% is the point of this repo. A pipeline that reports 100% is either being fed clean data or lying to you.

---

## Why this exists

Surgical robotics platforms generate rich data — procedure telemetry, instrument events, device inventory, physiologic streams. Almost none of it is exchangeable as emitted. It's shaped for engineering: high-frequency, denormalised, nullable, and coded in a **local vocabulary that no receiving system has ever heard of**.

Getting it into an EHR, a surgical registry, or a post-market surveillance system means crossing into FHIR. That crossing is where healthcare interoperability actually lives, and it is mostly not a technical problem — it's a **semantics and governance** problem.

This repo is a working demonstration of that crossing, built to show what I'd actually own as a technical leader in a medtech/health-IT org: not "can you emit JSON with `resourceType`," but *do you know what gets destroyed on the way, and do you refuse to hide it.*

**No PHI. No real patient data. No proprietary schema.** Everything here is synthetic and vendor-neutral by construction — see [Constraints](#constraints-and-what-they-taught-me).

---

## Architecture

```
  Source system (vendor-shaped)          Interop layer                FHIR R4B
  ─────────────────────────────          ─────────────                ────────
  SurgicalCase                    ┌──> terminology.py  ──┐            Patient
    ├─ procedure_local_code       │      concept map     │            Encounter
    ├─ patient_mrn                │      + trust status  │            Procedure
    ├─ laterality?                │                      ├──────────> Device
    ├─ DeviceRecord[]        ─────┤   mapping.py         │            Observation
    ├─ TelemetryEvent[]           │      + MappingIssue  │                │
    └─ PhysioSample[]             │                      │                v
                                  └──> quality.py     ───┘        transaction Bundle
                                         report                          │
                                                                         v
                                                                   FastAPI FHIR API
                                                                   /metadata
                                                                   /Procedure?...
```

| Module | Responsibility |
|---|---|
| `source_schema.py` | What the OR platform emits. Deliberately not FHIR. |
| `generator.py` | Synthetic cases **with realistic defects** injected. |
| `terminology.py` | Local → SNOMED/LOINC/UCUM bindings, each with a trust status. |
| `mapping.py` | Source → FHIR. Records every lossy decision as a `MappingIssue`. |
| `quality.py` | The governance artefact. What % is exchangeable, and where it degrades. |
| `store.py` / `api.py` | Read-only FHIR REST surface + CapabilityStatement. |

---

## Quickstart

```bash
git clone https://github.com/<you>/surgical-fhir-pipeline
cd surgical-fhir-pipeline
pip install -r requirements.txt

# Generate cases, map to FHIR, emit bundle + quality report
PYTHONPATH=src python scripts/generate.py -n 25

# Serve the FHIR API
PYTHONPATH=src uvicorn surgical_fhir.api:app --reload

# Tests (19)
PYTHONPATH=src pytest tests/ -q
```

```bash
curl localhost:8000/metadata                        # CapabilityStatement
curl localhost:8000/Procedure                       # searchset Bundle
curl "localhost:8000/Observation?patient=<id>&code=8867-4"
curl localhost:8000/quality-report                  # the governance artefact
curl "localhost:8000/Procedure?performer=Dr-X"      # -> OperationOutcome, 400
```

---

## The seven interoperability problems this pipeline hits

Each of these is a real failure mode. Each has a decision behind it, and I'd rather defend the decision than hide the problem.

### 1. Local procedure codes have no SNOMED home

The platform emits `RAS-CHOL-01`. FHIR wants a SNOMED CT concept. I map it to `45595009 | Cholecystectomy` — **and that mapping is lossy in a way that would fail a surgical registry.** It drops both the minimally-invasive approach *and* the robotic assistance. In SNOMED those are frequently post-coordinated expressions or separate qualifiers, not a pre-coordinated concept.

**Decision:** map to the parent, flag it `PROVISIONAL`, and preserve the full clinical intent in `Procedure.code.text`. The free text is the safety net for what the code destroyed.

### 2. I don't have a SNOMED CT licence, and I'm not going to pretend

Every binding in `terminology.py` carries a `BindingStatus`: `VERIFIED` or `PROVISIONAL`.

- **LOINC vital-sign codes → VERIFIED.** Freely published, stable, canonical in US Core. I'll stand behind them.
- **SNOMED procedure codes → PROVISIONAL.** Plausible, developer-asserted, unvalidated against a licensed release.

In production these bindings are authored by a **clinical informaticist against a terminology server** (Snowstorm, Ontoserver) and reviewed — not written by the pipeline engineer at 11pm. A repo that silently ships developer-guessed SNOMED codes as fact is modelling the exact behaviour that produces unusable registry data.

There's a test asserting the procedure bindings stay marked provisional. If it ever fails, someone promoted a binding without doing the work.

### 3. Estimated blood loss: I couldn't find a LOINC code, so I didn't invent one

EBL is emitted with a **local code** under `urn:local:or-metrics`, not a fabricated LOINC. It will fail a US Core validator — loudly, which is correct. A made-up LOINC would pass validation and be wrong, which is the worst of both worlds.

### 4. Unmapped code → drop the case, don't degrade it

The generator emits `RAS-COLEC-99` (~8% of cases) — a procedure profile the device team shipped without telling informatics. **The single most common interop failure in the field.**

The tempting move is a text-only `CodeableConcept`. That's wrong: an uncoded procedure in a clinical record is worse than an absent one, because it's *invisible to every query that matters* while still looking like data. The case is dropped, counted, and reported.

### 5. Missing laterality is a wrong-site-surgery-class defect

Inguinal hernia repair with no laterality. SNOMED code carries no side; it must ride on `Procedure.bodySite`.

**Decision:** ERROR, drop the case. Defaulting laterality is unthinkable. Emitting without it hands a receiving system a record it cannot safely act on.

### 6. FHIR is not a time-series store

1 Hz telemetry × 4 hours × 5 metrics = **72,000 Observations for one case.** Mapping device telemetry 1:1 to FHIR resources is the classic architectural mistake here.

FHIR is an *exchange* format. In production the stream lives in a time-series DB and FHIR carries summaries or `SampledData`. Here I downsample to 40/case and say so in the report. Knowing what *not* to put in FHIR is the senior judgement.

### 7. Sensor artefacts must never enter a chart as normal readings

A pulse-ox glitch reports SpO₂ of 4%. Bounds-checked values outside physiologic range are emitted as `status: entered-in-error` with a `dataAbsentReason` and **no value** — the record of the failure survives, the bad number doesn't.

Related: unit handling. `37` is a normal temperature or a hypothermic emergency depending on whether you meant `Cel` or `[degF]`, and nothing in the JSON tells you. Units go through an explicit UCUM map that raises on unknown input.

---

## A bug I shipped and what it taught me

The first version reconciled to **22 Procedures against 20 Patients.**

`map_procedure()` built the Procedure *before* the laterality check ran. When the check raised, `map_case()` returned that result object early — with the Procedure still attached. Two orphan Procedures went out referencing a Patient and Encounter that were never created. A receiving FHIR server would either reject the bundle or, worse, accept it.

The function had a docstring explicitly claiming to prevent this.

The fix was three lines. The lesson was the quality report: **the orphan was only visible because the pipeline counts its own output by resource type and I looked at the numbers.** Referential integrity is now a test (`test_referential_integrity_no_orphan_resources`), not a comment.

This is why the report is a first-class deliverable rather than a nice-to-have. Prose does not enforce invariants.

---

## Design decisions worth arguing about

| Decision | Rationale | The counter-argument |
|---|---|---|
| Drop cases on unmapped code | Uncoded clinical data is worse than absent data | You're throwing away 8% of surgical volume; a registry might prefer text-coded records to none |
| Drop on missing laterality | Wrong-site-surgery-class defect | Arguably over-aggressive — could emit with a `dataAbsentReason` on `bodySite` |
| Transaction Bundle uses `PUT` not `POST` | Idempotent. Retries and backfills are *normal* in health integration; a non-idempotent loader creates duplicate clinical records | Requires client-assigned IDs, which some servers resist |
| Search raises on unsupported params | Silently ignoring a filter is how a client shows another patient's data | Stricter than most real servers |
| In-memory store, not HAPI | The value here is the mapping + governance layer; rewriting a FHIR server would be the wrong instinct | Not production-representative |

---

## Constraints, and what they taught me

- **No PHI, ever.** Never touching real patient data is not a limitation of this project — it's the discipline the domain demands. The `_pseudonymise()` docstring is explicit that a deterministic MRN hash is **not de-identification**: it's still a linkable identifier under HIPAA Safe Harbor, and it's only defensible here because the input is synthetic. Real de-identification is a separate service with key custody, an audit trail, and expert determination. A test asserts no raw MRN survives into the output.
- **Vendor-neutral by construction.** No employer's product name, schema, or telemetry format appears anywhere. The source schema is my synthesis of what an OR platform generically emits. If a portfolio repo looks like it leaked a proprietary schema, that's a hiring signal — the wrong one.
- **R4B, stated plainly.** This uses `fhir.resources.R4B`. R4B is the maintenance release of R4; for Patient / Encounter / Procedure / Device / Observation the R4→R4B differential is immaterial. Saying "R4B" is more accurate than claiming "R4" and hoping nobody checks.

---

## What I'd build next

1. **Validate against a real terminology server** (Snowstorm) and promote bindings `PROVISIONAL → VERIFIED` with evidence — turning the honest gap into a closed loop.
2. **Profile against US Core / a surgical IG**, run the official validator in CI, and let the build fail on conformance regression.
3. **Swap the in-memory store for HAPI FHIR** and prove the bundles actually load into a real server.
4. **`$everything` and a Provenance resource** per case — auditability is the thing regulated customers ask about first.
5. **Time-series split:** telemetry to a TSDB, FHIR `SampledData` for the exchange summary.

---

## License

MIT. Synthetic data only. SNOMED CT is licensed content (SNOMED International; free in member territories via UMLS). LOINC is used under the LOINC licence. Codes here are reproduced as identifiers for interoperability demonstration — the ordinary use of a code system in an implementation — and the provisional ones must not be treated as clinically validated.
