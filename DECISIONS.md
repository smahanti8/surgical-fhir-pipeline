# Decision log

The decisions in this pipeline worth arguing about, one entry each: the
context that forced a choice, the choice, why, and the strongest argument
against it. If a counter-argument ever wins, the entry gets superseded here —
not silently rewritten.

---

## D1. Drop cases on unmapped procedure codes

**Context.** ~8% of generated cases carry `RAS-COLEC-99`, a local procedure
code with no binding in the concept map — simulating a device team shipping a
new procedure profile without telling informatics, the single most common
interop failure in the field. The tempting fallback is a text-only
`CodeableConcept`.

**Decision.** The case is dropped from the FHIR output entirely, counted, and
reported (`UnmappedConceptError` → ERROR → `cases_dropped`).

**Rationale.** An uncoded procedure in a clinical record is worse than an
absent one: it is invisible to every query that matters — registry pulls,
quality measures, billing — while still looking like data. Uncoded clinical
data is worse than absent data.

**Counter-argument.** This throws away 8% of surgical volume; a registry might
prefer text-coded records to none.

---

## D2. Drop on missing laterality

**Context.** Inguinal hernia repair is a laterality-relevant procedure, and
~20% of such cases arrive with no side recorded. The SNOMED code carries no
side; it must ride on `Procedure.bodySite`.

**Decision.** Missing laterality on a laterality-relevant procedure is an
ERROR and the case is dropped. Never defaulted.

**Rationale.** This is a wrong-site-surgery-class data defect. Emitting the
record without a side hands a receiving system data it cannot safely act on;
defaulting a side is unthinkable.

**Counter-argument.** Arguably over-aggressive — the case could be emitted
with a `dataAbsentReason` on `bodySite`, preserving the rest of the record.

---

## D3. Transaction Bundle uses `PUT`, not `POST`

**Context.** The pipeline emits a FHIR transaction Bundle intended for
loading into any FHIR server. Loaders get retried and backfilled as a normal
operational matter in healthcare integration.

**Decision.** Every bundle entry uses `PUT` with a client-assigned id
(idempotent upsert) rather than `POST`.

**Rationale.** Replaying a bundle must be safe. A non-idempotent loader
creates duplicate clinical records, and duplicate clinical records are a
patient-safety issue.

**Counter-argument.** Requires client-assigned IDs, which some servers
resist.

---

## D4. Search raises on unsupported parameters

**Context.** The API supports a small set of search parameters per resource
type. A client may send parameters the server does not implement — e.g.
`GET /Procedure?performer=Dr-X`.

**Decision.** Unsupported parameters return a 400 with an OperationOutcome
instead of being ignored.

**Rationale.** Silently ignoring a filter is how a client ends up displaying
another patient's data. The FHIR spec's guidance is that a server SHOULD
signal parameters it does not handle.

**Counter-argument.** Stricter than most real-world servers, which commonly
ignore unknown parameters and return the unfiltered set.

---

## D5. In-memory store, not HAPI

**Context.** The mapped resources need a FHIR REST surface to prove they are
genuinely exchangeable. A real deployment would put HAPI FHIR, Firely, or
Medplum here.

**Decision.** A minimal in-memory store implements just enough of the
RESTful API (read, type-level search, CapabilityStatement) and no more.

**Rationale.** The value of this repo is the mapping + governance layer.
Rewriting a FHIR server would be the wrong instinct — knowing what NOT to
build is part of the judgement being demonstrated.

**Counter-argument.** Not production-representative; bundles are not proven
against a real server's validation and reference-checking behaviour.

---

## D6. Referential integrity is enforced by tests, not prose

**Context.** The first version of this pipeline reconciled to **22 Procedures
against 20 Patients**. `map_procedure()` built the Procedure *before* the
laterality check ran; when the check raised, `map_case()` returned that
result object early — with the Procedure still attached. Two orphan
Procedures went out referencing a Patient and Encounter that were never
created. A receiving FHIR server would either reject the bundle or, worse,
accept it. The function had a docstring explicitly claiming to prevent this.

**Decision.** The invariant moved from documentation into the test suite
(`test_referential_integrity_no_orphan_resources`), and the quality report —
which counts output by resource type, and is how the orphan was caught —
stays a first-class deliverable rather than a nice-to-have.

**Rationale.** The fix was three lines; the lesson was structural. Prose does
not enforce invariants; assertions do. The orphan was only visible because
the pipeline counts its own output and the numbers were read.

**Counter-argument.** Docstrings plus code review should catch this class of
bug. They didn't — which is the point of the entry.

---

## D7. BDD specs via pytest-bdd for four invariants only, not the full suite

**Context.** The test suite enforces its invariants correctly, but nothing in
`tests/` is readable by a non-engineer reviewing what the pipeline actually
guarantees — a governance auditor reading `test_unmapped_case_is_dropped_entirely`
has to read Python to know what it asserts.

**Decision.** Add `pytest-bdd` and four Gherkin feature files, covering
exactly the four invariants named for this chapter: unmapped procedure code
drops the case (D1), missing laterality on a sided procedure drops the case
(D2), an out-of-range physiological reading is never emitted as a final
Observation, and no raw MRN appears in output. Step definitions call
`mapping.py` and `quality.py` directly; they do not duplicate that logic.

**Rationale.** A product-readable specification for the four invariants a
reviewer is most likely to ask about, without introducing a second
implementation of pipeline behaviour — the Gherkin text is a label over
existing assertions, not a parallel path that could drift from them.

**Counter-argument.** Two testing paradigms in one repo is cognitive
overhead; a determined skeptic could argue pytest already covers this exact
behaviour and Gherkin adds a translation layer that can go stale if a step
definition is ever loosened to stop calling the real functions.

---

## D8. Deploy target: Render, free tier

**Context.** The pipeline needed a real, CI-triggerable, reachable deployment to
prove it runs outside a laptop, without asking for a credit card or a billing
account just to demonstrate a synthetic-data pipeline. Free-tier terms were
checked live against Render's own documentation on 2026-09-26, not from memory:
750 free instance-hours/month, the instance spins down after 15 minutes with no
inbound traffic, cold start is roughly one minute, and the filesystem is
ephemeral — any writes are lost on every redeploy, restart, or spin-down (no
persistent disk on the free instance type). Render's docs do not explicitly
state whether prebuilt-image deploys are supported on the free instance type,
but nothing found excludes it either — the Git-based and Docker-based deploy
docs both point to the same free-tier limitations page, and the "Free web
services" limitation list restricts disk, hours, scaling, and shell access, not
deploy source.

**Decision.** Deploy to Render, free instance type, from a prebuilt image
pushed to GHCR by CI. CI triggers a redeploy via Render's deploy hook, passing
`imgURL=<new digest>` so Render pulls the exact image CI just pushed rather than
whatever tag was last configured in the dashboard.

**Rationale.** No card, no billing account, deploy-hook-driven redeploy fits
the existing CI shape, and the free tier's own limitations (ephemeral
filesystem, spin-down) already match this repo's stated scope — a
demonstrator, not a system of record.

**Counter-argument.** Fly.io's pricing page (checked 2026-09-26) shows no free
tier: every organization needs a credit card on file, and one small
shared-cpu machine with 256 MB costs roughly $2.59/month. Google Cloud Run
needs a project with billing enabled, with its free tier applying inside it;
that was not re-verified on the pricing page when this was written. Both give
more control (always-on, a real disk) than Render's free tier, and that
control was traded away here to avoid putting a payment method on file for a
synthetic-data demo.

---

## D9. KPI store stays ephemeral on this deployment

**Context.** `kpi_store.py` persists a SQLite ledger at `SURGICAL_FHIR_KPI_DB`
(default `governance_kpis.db`). Render's free instance type has no persistent
disk (D8): that file is recreated empty on every redeploy, restart, or
15-minute spin-down.

**Decision.** Accept this. No paid Render disk, no external managed database,
is added to keep KPI history across restarts on this deployment.

**Rationale.** This matches the repo's existing scope-honesty stance — `store.py`
is already documented as in-memory with no persistence (`ARCHITECTURE.md §6`).
Extending that same honesty to the KPI ledger on this specific host, rather
than quietly implying it is durable, is consistent with the rest of this
project's posture.

**Counter-argument.** KPI trend history — the one thing the store exists to
accumulate across runs — resets on every spin-down, which undercuts the
"governance over time" pitch for exactly the deployment meant to demonstrate
it.
