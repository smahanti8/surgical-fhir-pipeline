"""Tests for KPI persistence and the /governance-kpis endpoint.

Prose does not enforce invariants; assertions do.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from surgical_fhir.api import create_app
from surgical_fhir.generator import generate_cases
from surgical_fhir.kpi_store import KPIStore
from surgical_fhir.quality import build_report


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_kpis.db"


@pytest.fixture
def sample_report():
    cases = generate_cases(n=10, seed=42)
    report, _ = build_report(cases)
    return report


# ------------------------------------------------------------------ persist


def test_kpi_persist_writes_correct_scalar_values(tmp_db, sample_report):
    store = KPIStore(db_path=tmp_db)
    run_id = store.persist(sample_report)
    runs = store.get_trend()

    assert len(runs) == 1
    r = runs[0]
    assert r["run_id"] == run_id
    assert r["n_cases"] == sample_report.cases_in
    assert r["n_exchangeable"] == sample_report.cases_exchangeable
    assert r["n_dropped"] == sample_report.cases_dropped
    assert abs(r["exchangeable_rate"] - sample_report.exchangeable_rate) < 1e-4


def test_kpi_persist_writes_trust_mix(tmp_db, sample_report):
    store = KPIStore(db_path=tmp_db)
    store.persist(sample_report)
    r = store.get_trend()[0]

    tc = sample_report.terminology_coverage
    assert r["trust_mix"]["verified"] == tc.get("verified", 0)
    assert r["trust_mix"]["provisional"] == tc.get("provisional", 0)
    assert r["trust_mix"]["total"] == tc.get("total", 0)


def test_drop_reasons_are_error_only(tmp_db, sample_report):
    """drop_reasons must reflect error_by_element, not the all-severity mix."""
    store = KPIStore(db_path=tmp_db)
    store.persist(sample_report)
    r = store.get_trend()[0]

    # error_by_element is the source of truth for what we persisted
    assert r["drop_reasons"] == sample_report.error_by_element
    # Verify it differs from the all-severity issues_by_element when warnings exist
    assert sample_report.error_by_element != sample_report.issues_by_element


def test_kpi_persist_is_idempotent_on_same_run_id(tmp_db, sample_report):
    store = KPIStore(db_path=tmp_db)
    store.persist(sample_report, run_id="fixed-id")
    store.persist(sample_report, run_id="fixed-id")  # INSERT OR REPLACE
    assert len(store.get_trend()) == 1


def test_kpi_persist_accumulates_multiple_runs(tmp_db, sample_report):
    store = KPIStore(db_path=tmp_db)
    store.persist(sample_report, run_id="run-a")
    store.persist(sample_report, run_id="run-b")
    runs = store.get_trend()

    assert len(runs) == 2
    assert {r["run_id"] for r in runs} == {"run-a", "run-b"}


def test_kpi_trend_limit_param(tmp_db, sample_report):
    store = KPIStore(db_path=tmp_db)
    for i in range(5):
        store.persist(sample_report, run_id=f"run-{i:02d}")
    assert len(store.get_trend(limit=3)) == 3


def test_kpi_trend_returns_oldest_first(tmp_db, sample_report):
    """Rows with the same timestamp fall back to run_id ASC."""
    store = KPIStore(db_path=tmp_db)
    store.persist(sample_report, run_id="run-aaa")
    store.persist(sample_report, run_id="run-zzz")
    runs = store.get_trend()
    # Both rows have nearly identical timestamps; run_id tiebreak is deterministic
    run_ids = [r["run_id"] for r in runs]
    assert run_ids == sorted(run_ids)


# ------------------------------------------------------------------ endpoint


@pytest.fixture
def client(tmp_db):
    app = create_app(n_cases=10, seed=42, kpi_db=tmp_db)
    return TestClient(app)


def test_governance_endpoint_returns_200(client):
    resp = client.get("/governance-kpis")
    assert resp.status_code == 200


def test_governance_endpoint_schema_version(client):
    data = client.get("/governance-kpis").json()
    assert data["schema_version"] == "1"


def test_governance_endpoint_contains_one_run_on_startup(client):
    data = client.get("/governance-kpis").json()
    assert data["total_runs"] == 1
    assert len(data["runs"]) == 1


def test_governance_endpoint_run_shape(client):
    r = client.get("/governance-kpis").json()["runs"][0]
    assert "run_id" in r
    assert "timestamp" in r
    assert "exchangeable_rate" in r
    assert "trust_mix" in r
    assert set(r["trust_mix"]) == {"verified", "provisional", "total"}
    assert "drop_reasons" in r
    assert "issues_by_severity" in r


def test_governance_endpoint_limit_param(tmp_db):
    app = create_app(n_cases=10, seed=42, kpi_db=tmp_db)
    client = TestClient(app)

    # Persist a second run manually to give the limit param something to trim
    cases = generate_cases(n=10, seed=99)
    report, _ = build_report(cases)
    KPIStore(db_path=tmp_db).persist(report)

    resp = client.get("/governance-kpis?limit=1")
    assert resp.status_code == 200
    assert len(resp.json()["runs"]) == 1


def test_governance_endpoint_generated_at_is_present(client):
    data = client.get("/governance-kpis").json()
    assert "generated_at" in data
    assert data["generated_at"]  # non-empty string
