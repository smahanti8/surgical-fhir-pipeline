# PHI Handling Rules — surgical-fhir-pipeline

These rules are specific to this repository's healthcare data context.
Generic security rules (dangerous commands, secret files) live in ~/.claude/rules/security.md.

---

## PHI boundary

This repository contains no real patient data. All input is synthetic.
`_pseudonymise` in `mapping.py` applies SHA-256 to the MRN and is explicitly
documented as NOT de-identification — still linkable under HIPAA Safe Harbor
if the input were real.

1. Never remove or weaken `test_no_raw_mrn_leaks_into_output`. It is the
   PHI boundary assertion for this pipeline.

2. Never modify `_pseudonymise` without first entering plan mode, updating
   its docstring, and updating or replacing `test_no_raw_mrn_leaks_into_output`.

3. Never add a log statement, print, or file write that could emit `patient_mrn`,
   `patient_id`, or the derived pseudonym in plaintext.

4. Never add real patient data to generator.py, tests, fixtures, or sample files.
   All generated data must be provably synthetic (random seed, fictional MRNs).

---

## Terminology governance

1. Never change `BindingStatus.PROVISIONAL` to `BindingStatus.VERIFIED` for any
   existing binding without a documented clinical validation source.

2. Never add a binding with `BindingStatus.VERIFIED` without linking to the
   licence or validation evidence in a code comment.

3. `test_procedure_bindings_are_honestly_marked_provisional` must pass after
   any change to `terminology.py`. Never remove or soften it.
