# Test Strategy

This document explains why the test suite in `tests/` is shaped the way it
is: why each category in [`.claude/rules/testing.md`](.claude/rules/testing.md)'s
taxonomy exists, why the five tests on that file's never-remove list are
load-bearing, and what is not yet covered. It does not restate
`.claude/rules/testing.md` — read that file first for the taxonomy table and
the rules for adding tests.

---

## Philosophy

> Prose does not enforce invariants; assertions do.
> — `tests/test_pipeline.py`, line 1

This suite exists because a docstring already claimed to prevent the bug
that shipped anyway (see D6 in `DECISIONS.md`). The tests are the
enforcement mechanism; the documentation — including this file — describes
what they enforce and why, not the other way around.

---

## Why each taxonomy category exists

**Terminology** — binding lookups (`procedure_binding`, `observation_binding`,
`ucum_unit`) are the anti-corruption boundary between the vendor's local
vocabulary and standard codes. Without this category, a change to
`terminology.py` could silently return a wrong code, or silently promote a
`PROVISIONAL` binding to `VERIFIED`, with nothing noticing either.

**Mapping** — `SurgicalCase -> MappingResult` is where every quality gate
lives (drop-on-unmapped-code, drop-on-missing-laterality, entered-in-error
on out-of-range physio). Without this category, a refactor of `mapping.py`
could quietly widen or narrow the drop/degrade boundary and nothing would
fail.

**Quality** — `QualityReport` is the governance artefact the rest of the
repo's argument depends on. If its arithmetic were wrong — counts not
reconciling, resources double-counted — nothing else in the suite would
catch it; only tests that check the report's own internal consistency can.

**API** — HTTP status codes and response shapes (`OperationOutcome` on
error, `searchset` Bundle on search) are the FHIR conformance contract.
Without this category, `api.py` could regress to FastAPI's default error
body or drop the `CapabilityStatement`, and no other category would notice,
because none of the others touch HTTP.

**Invariants** — structural guarantees that only hold across the *whole*
pipeline output, not any single function's return value — referential
integrity being the example. These invariants are invisible to unit-level
tests on `mapping.py` alone, because the orphan-Procedure bug they guard
against was exactly that: each function did what its own docstring said,
and the break was only visible in the combined output.

**Security** — PHI and pseudonymisation checks. This category exists because
every other category could pass while a raw MRN still leaked into output —
none of the other five test what does *not* appear in a resource.

---

## Why each never-remove test is load-bearing

- **`test_referential_integrity_no_orphan_resources`** — the regression test
  for D6: `map_procedure` once built a `Procedure` object before the
  laterality check ran, and an early return on failure leaked it with
  references to a Patient and Encounter that were never emitted. The bug
  shipped past both a docstring and code review; this test is what would
  have caught it, and remains the only thing that would catch a
  reintroduction.
- **`test_no_raw_mrn_leaks_into_output`** — asserts the PHI boundary
  directly: that every MRN generated for a run is absent from the
  serialised transaction bundle. Without it, `_pseudonymise` could be
  bypassed, disabled, or silently changed to a pass-through, and every
  other test would still pass.
- **`test_procedure_bindings_are_honestly_marked_provisional`** — asserts
  that the three named SNOMED procedure bindings stay `PROVISIONAL`. This
  is the test that enforces the repo's central honesty claim about its own
  terminology — that nothing gets promoted to `VERIFIED` without the
  clinical validation source `.claude/rules/phi-handling.md` requires.
- **`test_implausible_physio_value_is_not_emitted_as_final`** — asserts that
  any Observation with `status == "entered-in-error"` has both a `None`
  `valueQuantity` and a populated `dataAbsentReason`. Without it, a future
  change to `map_observations` could reintroduce the exact failure mode the
  bounds check exists to prevent: a sensor artefact reaching a chart as if
  it were a real reading.
- **`test_pipeline_is_reproducible`** — asserts that `generate_cases ->
  build_report -> to_transaction_bundle` produces an identical
  `QualityReport` and an identical serialized transaction bundle across two
  runs with `seed=42`, and that `seed=43` differs from both, so the test
  cannot pass by comparing two constants. It only compares runs inside a
  single process, so it would not catch a source of nondeterminism that
  depends on cross-process state, such as hash-order effects. Today, CI's
  own reproducibility check runs `scripts/generate.py -n 25` once and does
  not compare it against a second run — this test covers the pipeline
  functions directly, but closing the equivalent gap in CI itself is a
  separate, not-yet-done piece of work.

---

## Coverage gaps — stated honestly

This list is partial. It was found through direct inspection of `tests/`
while writing this document, not the output of a coverage tool — it names
what is absent, not everything that could be tested.

1. **`fullUrl` is never asserted on.** `to_transaction_bundle`
   (`mapping.py:549`) sets each Bundle entry's `fullUrl` to
   `urn:uuid:{ResourceType}-{id}` — which is not a UUID. No test in
   `tests/` references `fullUrl` in any form.

2. **Per-resource-type search parameters are not tested per type.**
   `api.py`'s `SUPPORTED` dict advertises a different parameter list for
   each resource type (e.g. `Patient` supports only `_count`), but
   `store.search` (`store.py:43`) validates against one parameter set
   shared by every resource type. No test exercises a parameter that is
   valid for one resource type and invalid for another.

3. **`Provenance` is never read back through the API.** It is asserted via
   direct `FHIRStore.read()` calls and via inclusion in `$everything`
   Bundles, but no test issues `GET /Provenance/{id}` through the API
   client — and `Provenance` does not appear in `SUPPORTED`, so that
   request currently returns 404.

4. **The laterality rule has only ever been exercised against one
   procedure code.** `RAS-HERN-01` is the only laterality-relevant entry in
   `_PROCEDURE_BINDINGS`, and it is the only code any test uses to exercise
   the missing-laterality drop path. Nothing confirms the rule generalizes
   to a second sided procedure rather than being coupled to that one code.

5. **The case-level consequence of an unmapped unit is untested.**
   `test_unknown_unit_raises` calls `tx.ucum_unit()` directly and confirms
   the terminology layer fails loudly. No test runs a case through
   `map_case`/`build_report` with an unmapped unit to confirm the resulting
   behaviour: the case stays exchangeable, and the issue lands in
   `error_by_element` without appearing in `cases_dropped`.
