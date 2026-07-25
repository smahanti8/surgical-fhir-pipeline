# Testing Rules — surgical-fhir-pipeline

---

## The test suite's philosophy

From `tests/test_pipeline.py`, line 1:
> Prose does not enforce invariants; assertions do.

These rules exist to keep that true as the test suite grows.

---

## Tests that must never be removed

These tests enforce load-bearing invariants. Removing them would allow silent
regressions that are hard to detect by reading the code.

| Test | Why it must not be removed |
|---|---|
| `test_referential_integrity_no_orphan_resources` | Regression test for a real orphan-Procedure bug. Every Procedure must have a Patient and Encounter. |
| `test_no_raw_mrn_leaks_into_output` | PHI boundary assertion. Without it, `_pseudonymise` could be silently bypassed. |
| `test_procedure_bindings_are_honestly_marked_provisional` | Governance invariant. Prevents silent trust promotion of SNOMED bindings. |
| `test_implausible_physio_value_is_not_emitted_as_final` | Ensures out-of-range sensor readings become `entered-in-error`, not final Observations. |
| `test_pipeline_is_reproducible` | Guards the `seed=42` reproducibility guarantee used in CI. |

If a refactor makes one of these tests fail, fix the implementation — do not
soften or remove the test.

---

## Rules for adding new tests

1. **New quality gates require a test first.** If you add a new drop condition
   or a new severity escalation path in `mapping.py`, write the failing test
   before the implementation.

2. **New terminology bindings require at least one test.** Add a test that
   asserts the binding returns the expected SNOMED/LOINC code and the correct
   `BindingStatus`.

3. **Regression tests for bugs must name the bug.** If a test is added to catch
   a specific bug, include a comment that names the failure mode and when it was
   found. See `test_referential_integrity_no_orphan_resources` for the pattern.

4. **No mocking of core pipeline components.** Do not mock `terminology.py`,
   `mapping.py`, or `quality.py` in tests for those same modules. Test them
   with real inputs. Mocking is acceptable in API tests where you want to
   isolate the REST layer.

5. **Tests must be deterministic.** Any test that uses `generate_cases` must
   pass an explicit `seed`. Never rely on random state.

---

## Test taxonomy

| Category | Pattern | Examples |
|---|---|---|
| **Terminology** | Input code → expected standard code + trust | `test_verified_hr_binding_is_trusted` |
| **Mapping** | SurgicalCase → MappingResult properties | `test_unmapped_procedure_code_drops_case` |
| **Quality** | All MappingResults → QualityReport assertions | `test_quality_report_totals_are_consistent` |
| **API** | HTTP request → status code + response shape | `test_metadata_returns_capability_statement` |
| **Invariants** | Structural guarantees across the full pipeline | `test_referential_integrity_no_orphan_resources` |
| **Security** | PHI, pseudonymisation, PII leak checks | `test_no_raw_mrn_leaks_into_output` |

---

## Running tests

```bash
# Full suite (must pass before any commit)
PYTHONPATH=src pytest tests/ -q

# With verbose output for debugging
PYTHONPATH=src pytest tests/ -v

# A single test during development
PYTHONPATH=src pytest tests/test_pipeline.py::test_referential_integrity_no_orphan_resources -v
```

The CI reproducibility check also runs:
```bash
PYTHONPATH=src python scripts/generate.py -n 25 > /tmp/out1.txt
PYTHONPATH=src python scripts/generate.py -n 25 > /tmp/out2.txt
diff /tmp/out1.txt /tmp/out2.txt
```
This must produce no diff.

---

## What NOT to add

- Do not add `@pytest.mark.skip` without a linked issue and an expiry plan.
- Do not use `assert True` or empty test bodies as placeholders.
- Do not write tests that only test Python builtins or the testing framework itself.
- Do not duplicate the 25 full-case pipeline as a test — use the targeted single-case
  and structural tests instead.
