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
