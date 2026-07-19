"""Tests.

The referential-integrity test exists because the invariant it checks was
documented in a docstring, believed, and violated anyway. Prose does not enforce
invariants; assertions do.
"""

from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import pytest
from fastapi.testclient import TestClient

from surgical_fhir import terminology as tx
from surgical_fhir.api import create_app
from surgical_fhir.generator import generate_cases
from surgical_fhir.mapping import Severity, map_case, to_transaction_bundle
from surgical_fhir.quality import build_report


# ------------------------------------------------------------ terminology


def test_unmapped_procedure_code_raises_not_silently_passes():
    with pytest.raises(tx.UnmappedConceptError):
        tx.procedure_binding("RAS-DOES-NOT-EXIST")


def test_vital_sign_bindings_are_verified():
    for metric in ("hr_bpm", "sbp_mmhg", "dbp_mmhg", "spo2_pct", "temp_c"):
        assert tx.observation_binding(metric).is_trusted


def test_procedure_bindings_are_honestly_marked_provisional():
    """If this ever fails, someone promoted a binding without a terminology
    server. That is the failure mode this repo is about."""
    for code in ("RAS-CHOL-01", "RAS-APPY-01", "RAS-HERN-01"):
        assert not tx.procedure_binding(code).is_trusted


def test_unknown_unit_raises():
    with pytest.raises(tx.UnmappedConceptError):
        tx.ucum_unit("furlongs")


# ------------------------------------------------------------ mapping


def test_unmapped_case_is_dropped_entirely():
    cases = [c for c in generate_cases(50, 42) if c.procedure_local_code == "RAS-COLEC-99"]
    assert cases, "generator should produce unmapped cases"
    result = map_case(cases[0])
    assert result.errors
    assert result.resources == []


def test_referential_integrity_no_orphan_resources():
    """Every subject/encounter reference must resolve within the same output.

    THIS IS THE REGRESSION TEST for the orphan-Procedure bug.
    """
    _, resources = build_report(generate_cases(50, 42))
    ids = {f"{r.__resource_type__}/{r.id}" for r in resources}

    for r in resources:
        for field in ("subject", "encounter"):
            ref = getattr(r, field, None)
            if ref is not None and ref.reference:
                assert ref.reference in ids, (
                    f"{r.__resource_type__}/{r.id}.{field} -> {ref.reference} is an "
                    "orphan reference"
                )
        used = getattr(r, "usedReference", None) or []
        for u in used:
            assert u.reference in ids, f"orphan usedReference {u.reference}"


def test_every_procedure_has_a_patient_and_encounter():
    _, resources = build_report(generate_cases(50, 42))
    counts: dict[str, int] = {}
    for r in resources:
        counts[r.__resource_type__] = counts.get(r.__resource_type__, 0) + 1
    assert counts["Procedure"] == counts["Patient"] == counts["Encounter"]


def test_implausible_physio_value_is_not_emitted_as_final():
    """A sensor artefact must never enter a chart as a normal reading."""
    _, resources = build_report(generate_cases(60, 42))
    for r in resources:
        if r.__resource_type__ != "Observation":
            continue
        if r.status == "entered-in-error":
            assert r.valueQuantity is None
            assert r.dataAbsentReason is not None


def test_no_raw_mrn_leaks_into_output():
    """PHI-adjacent identifiers must not survive the mapping."""
    cases = generate_cases(30, 42)
    mrns = {c.patient_mrn for c in cases}
    _, resources = build_report(cases)
    blob = to_transaction_bundle(resources).model_dump_json()
    for mrn in mrns:
        assert mrn not in blob, f"raw MRN {mrn} leaked into FHIR output"


def test_converted_open_collapses_to_completed_and_is_flagged():
    cases = [c for c in generate_cases(120, 7) if c.outcome == "converted_open"]
    assert cases
    result = map_case(cases[0])
    if not result.resources:
        pytest.skip("case dropped for an unrelated defect")
    proc = next(r for r in result.resources if r.__resource_type__ == "Procedure")
    assert proc.status == "completed"
    assert any(i.element == "Procedure.status" for i in result.issues)


def test_bundle_is_idempotent_upsert():
    _, resources = build_report(generate_cases(5, 42))
    bundle = to_transaction_bundle(resources)
    assert bundle.type == "transaction"
    for entry in bundle.entry:
        assert entry.request.method == "PUT"


def test_open_case_is_in_progress_not_fabricated():
    cases = [c for c in generate_cases(80, 42) if c.end_time is None]
    assert cases
    for c in cases:
        result = map_case(c)
        if not result.resources:
            continue
        enc = next(r for r in result.resources if r.__resource_type__ == "Encounter")
        assert enc.status == "in-progress"
        assert enc.period.end is None


# ------------------------------------------------------------ quality


def test_report_reconciles():
    report, resources = build_report(generate_cases(25, 42))
    assert report.cases_in == report.cases_exchangeable + report.cases_dropped
    assert report.resources_out == len(resources)
    assert report.cases_dropped > 0, "generator must exercise the drop path"


# ------------------------------------------------------------ API conformance


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(n_cases=15, seed=42))


def test_capability_statement(client):
    r = client.get("/metadata")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body["resourceType"] == "CapabilityStatement"
    assert body["fhirVersion"] == "4.3.0"


def test_search_returns_searchset_bundle(client):
    r = client.get("/Procedure")
    assert r.status_code == 200
    body = r.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "searchset"
    assert body["total"] == len(body.get("entry", []))


def test_unsupported_search_param_returns_operationoutcome(client):
    r = client.get("/Procedure?performer=Dr-Nobody")
    assert r.status_code == 400
    body = r.json()
    assert body["resourceType"] == "OperationOutcome"
    assert body["issue"][0]["severity"] == "error"


def test_not_found_returns_operationoutcome(client):
    r = client.get("/Procedure/proc-DOES-NOT-EXIST")
    assert r.status_code == 404
    assert r.json()["resourceType"] == "OperationOutcome"


def test_search_by_patient_filters(client):
    all_procs = client.get("/Procedure").json()
    pid = all_procs["entry"][0]["resource"]["subject"]["reference"].split("/")[-1]
    r = client.get(f"/Observation?patient={pid}")
    assert r.status_code == 200
    for e in r.json().get("entry", []):
        assert e["resource"]["subject"]["reference"].endswith(pid)


def test_read_roundtrip(client):
    procs = client.get("/Procedure").json()
    rid = procs["entry"][0]["resource"]["id"]
    r = client.get(f"/Procedure/{rid}")
    assert r.status_code == 200
    assert r.json()["id"] == rid
