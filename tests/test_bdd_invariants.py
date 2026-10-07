"""BDD step definitions for the four invariants named in DECISIONS.md D7.

Wires the Gherkin scenarios in tests/features/ to the pipeline's own
mapping.py and quality.py functions. No pipeline logic is duplicated here —
every assertion calls the real function and checks its actual output.
"""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from pytest_bdd import given, parsers, scenarios, then, when

from surgical_fhir.generator import generate_cases
from surgical_fhir.mapping import Severity, map_case, to_transaction_bundle
from surgical_fhir.quality import build_report

scenarios("features")


# ------------------------------------------------------------ shared Given


@given(
    parsers.parse(
        "a synthetic cohort of {n:d} surgical cases generated with seed {seed:d}"
    ),
    target_fixture="cohort",
)
def _cohort(n, seed):
    return generate_cases(n=n, seed=seed)


# --------------------------------------------------- unmapped-code scenario


@given(
    "at least one case in the cohort has an unmapped procedure code",
    target_fixture="target_case",
)
def _unmapped_case(cohort):
    candidates = [c for c in cohort if c.procedure_local_code == "RAS-COLEC-99"]
    assert candidates, "cohort must contain an unmapped-procedure-code case"
    return candidates[0]


# ------------------------------------------------ missing-laterality scenario


@given(
    "at least one case in the cohort has a sided procedure with no laterality recorded",
    target_fixture="target_case",
)
def _missing_laterality_case(cohort):
    candidates = [
        c
        for c in cohort
        if c.procedure_local_code == "RAS-HERN-01" and c.laterality is None
    ]
    assert candidates, "cohort must contain a missing-laterality case"
    return candidates[0]


# ------------------------------------------------- shared single-case When / Then


@when("that case is mapped to FHIR", target_fixture="mapping_result")
def _map_it(target_case):
    return map_case(target_case)


@then("the case produces no FHIR resources")
def _no_resources(mapping_result):
    assert mapping_result.resources == []


@then("the case's mapping issues include an ERROR on Procedure.code")
def _error_on_procedure_code(mapping_result):
    assert any(
        i.severity is Severity.ERROR and i.element == "Procedure.code"
        for i in mapping_result.issues
    )


@then("the case's mapping issues include an ERROR on Procedure.bodySite")
def _error_on_bodysite(mapping_result):
    assert any(
        i.severity is Severity.ERROR and i.element == "Procedure.bodySite"
        for i in mapping_result.issues
    )


# --------------------------------------------- out-of-range physio scenario


@when(
    "all cases are mapped and built into a quality report",
    target_fixture="report_and_resources",
)
def _build_report(cohort):
    return build_report(cohort)


@then("every entered-in-error Observation has no value")
def _no_value_on_error(report_and_resources):
    _, resources = report_and_resources
    for r in resources:
        if r.__resource_type__ == "Observation" and r.status == "entered-in-error":
            assert r.valueQuantity is None


@then("every entered-in-error Observation has a dataAbsentReason")
def _has_data_absent_reason(report_and_resources):
    _, resources = report_and_resources
    for r in resources:
        if r.__resource_type__ == "Observation" and r.status == "entered-in-error":
            assert r.dataAbsentReason is not None


# ------------------------------------------------------ no-raw-MRN scenario


@when(
    "all cases are mapped and assembled into a transaction bundle",
    target_fixture="bundle_json",
)
def _build_bundle(cohort):
    _, resources = build_report(cohort)
    return to_transaction_bundle(resources).model_dump_json()


@then("none of the cohort's raw patient MRNs appear in the bundle")
def _no_raw_mrn(cohort, bundle_json):
    for c in cohort:
        assert c.patient_mrn not in bundle_json
