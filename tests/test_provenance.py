"""Tests for Provenance generation and the Encounter/$everything endpoint.

Prose does not enforce invariants; assertions do.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from surgical_fhir.api import create_app
from surgical_fhir.generator import generate_cases
from surgical_fhir.mapping import map_case
from surgical_fhir.provenance import map_provenance
from surgical_fhir.store import FHIRStore


@pytest.fixture(scope="module")
def mapped_case():
    """First case that maps successfully with seed=42."""
    cases = generate_cases(n=25, seed=42)
    for case in cases:
        result = map_case(case)
        if result.resources:
            return case
    pytest.skip("No mapped case found in seed-42 batch")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = tmp_path_factory.mktemp("prov") / "kpis.db"
    app = create_app(n_cases=25, seed=42, kpi_db=db)
    return TestClient(app)


@pytest.fixture(scope="module")
def mapped_enc_id(client):
    """enc_id for the first Encounter in the store (all stored Encounters are mapped)."""
    resp = client.get("/Encounter")
    entries = resp.json()["entry"]
    assert entries, "No Encounters in store"
    return entries[0]["resource"]["id"]


# ------------------------------------------------------------------ Provenance unit


def test_provenance_id_matches_case_id(mapped_case):
    result = map_case(mapped_case)
    prov = map_provenance(mapped_case, result.resources)
    assert prov.id == f"prov-{mapped_case.case_id}"


def test_provenance_target_includes_procedure(mapped_case):
    result = map_case(mapped_case)
    prov = map_provenance(mapped_case, result.resources)
    refs = [e.reference for e in prov.target]
    assert any(r.startswith("Procedure/") for r in refs)


def test_provenance_target_includes_encounter(mapped_case):
    result = map_case(mapped_case)
    prov = map_provenance(mapped_case, result.resources)
    refs = [e.reference for e in prov.target]
    assert any(r.startswith("Encounter/") for r in refs)


def test_provenance_agent_identifies_pipeline(mapped_case):
    result = map_case(mapped_case)
    prov = map_provenance(mapped_case, result.resources)
    assert len(prov.agent) == 1
    assert "surgical-fhir-pipeline" in prov.agent[0].who.display


def test_provenance_activity_is_transform(mapped_case):
    result = map_case(mapped_case)
    prov = map_provenance(mapped_case, result.resources)
    assert prov.activity is not None
    codes = [c.code for c in prov.activity.coding]
    assert "TRANS" in codes


def test_provenance_first_entity_is_source_record(mapped_case):
    result = map_case(mapped_case)
    prov = map_provenance(mapped_case, result.resources)
    first = prov.entity[0]
    assert first.role == "source"
    assert mapped_case.case_id in (first.what.display or "")


def test_provenance_binding_entities_carry_trust_annotation(mapped_case):
    """Each binding entity must expose trust status in display (machine-readable
    extension is a bonus; display is the required fallback)."""
    result = map_case(mapped_case)
    prov = map_provenance(mapped_case, result.resources)
    binding_entities = prov.entity[1:]  # skip the source-record entity
    assert binding_entities, "Expected at least the procedure binding entity"
    for entity in binding_entities:
        assert "trust:" in (entity.what.display or ""), (
            f"Entity missing trust annotation in display: {entity.what.display}"
        )


def test_provenance_observations_deduplicated_by_metric(mapped_case):
    """Binding entity count = 1 (procedure) + distinct metrics, not observations."""
    result = map_case(mapped_case)
    prov = map_provenance(mapped_case, result.resources)

    distinct_metrics = {s.metric for s in mapped_case.physio}
    # entity[0] is the source record; entity[1] is the procedure; rest are obs bindings
    obs_binding_count = len(prov.entity) - 2  # subtract source-record + procedure
    assert obs_binding_count == len(distinct_metrics)


def test_provenance_is_loadable_into_store(mapped_case):
    result = map_case(mapped_case)
    prov = map_provenance(mapped_case, result.resources)
    fresh_store = FHIRStore()
    fresh_store.load([prov])
    assert fresh_store.read("Provenance", prov.id) is prov


def test_provenance_serialises_to_fhir_json(mapped_case):
    """model_dump_json must not raise — confirms fhir.resources accepts our structure."""
    result = map_case(mapped_case)
    prov = map_provenance(mapped_case, result.resources)
    payload = prov.model_dump_json()
    assert '"resourceType":"Provenance"' in payload
    assert f'"id":"prov-{mapped_case.case_id}"' in payload


# ------------------------------------------------------------------ $everything endpoint


def test_everything_returns_200_for_mapped_encounter(client, mapped_enc_id):
    resp = client.get(f"/Encounter/{mapped_enc_id}/$everything")
    assert resp.status_code == 200


def test_everything_returns_404_for_unknown_encounter(client):
    resp = client.get("/Encounter/enc-DOES-NOT-EXIST/$everything")
    assert resp.status_code == 404


def test_everything_404_body_is_operation_outcome(client):
    resp = client.get("/Encounter/enc-DOES-NOT-EXIST/$everything")
    assert resp.json()["resourceType"] == "OperationOutcome"


def test_everything_bundle_type_is_searchset(client, mapped_enc_id):
    resp = client.get(f"/Encounter/{mapped_enc_id}/$everything")
    data = resp.json()
    assert data["resourceType"] == "Bundle"
    assert data["type"] == "searchset"


def test_everything_total_matches_entry_count(client, mapped_enc_id):
    resp = client.get(f"/Encounter/{mapped_enc_id}/$everything")
    data = resp.json()
    assert data["total"] == len(data["entry"])


def test_everything_includes_encounter_patient_procedure(client, mapped_enc_id):
    resp = client.get(f"/Encounter/{mapped_enc_id}/$everything")
    types = {e["resource"]["resourceType"] for e in resp.json()["entry"]}
    assert {"Encounter", "Patient", "Procedure"} <= types


def test_everything_includes_provenance(client, mapped_enc_id):
    resp = client.get(f"/Encounter/{mapped_enc_id}/$everything")
    types = {e["resource"]["resourceType"] for e in resp.json()["entry"]}
    assert "Provenance" in types


def test_everything_provenance_id_matches_encounter(client, mapped_enc_id):
    resp = client.get(f"/Encounter/{mapped_enc_id}/$everything")
    provs = [
        e["resource"]
        for e in resp.json()["entry"]
        if e["resource"]["resourceType"] == "Provenance"
    ]
    assert len(provs) == 1
    case_id = mapped_enc_id[len("enc-"):]
    assert provs[0]["id"] == f"prov-{case_id}"


def test_everything_does_not_mix_cases(client):
    resp_list = client.get("/Encounter?_count=2")
    entries = resp_list.json()["entry"]
    if len(entries) < 2:
        pytest.skip("Need at least 2 mapped encounters for isolation test")
    enc_id_1 = entries[0]["resource"]["id"]
    enc_id_2 = entries[1]["resource"]["id"]

    ids1 = {e["resource"]["id"] for e in client.get(f"/Encounter/{enc_id_1}/$everything").json()["entry"]}
    ids2 = {e["resource"]["id"] for e in client.get(f"/Encounter/{enc_id_2}/$everything").json()["entry"]}

    assert enc_id_1 in ids1
    assert enc_id_1 not in ids2
    assert enc_id_2 in ids2
    assert enc_id_2 not in ids1
