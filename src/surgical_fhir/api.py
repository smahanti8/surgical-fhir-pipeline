"""FHIR-conformant REST API over the mapped resources.

Conformance choices that matter (and that most portfolio repos skip):

  - `GET /metadata` returns a CapabilityStatement. This is the FHIR handshake:
    a client discovers what you support here. A "FHIR API" without one is a JSON
    API wearing a costume.
  - Errors return an **OperationOutcome**, not FastAPI's default
    `{"detail": ...}`. Non-conformant error bodies break real clients.
  - Search returns a `searchset` Bundle with `total` and self link.
  - Content-Type is `application/fhir+json`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fhir.resources.R4B.bundle import Bundle, BundleEntry, BundleLink
from fhir.resources.R4B.capabilitystatement import CapabilityStatement
from fhir.resources.R4B.operationoutcome import OperationOutcome

from .generator import generate_cases
from .kpi_store import KPIStore
from .quality import build_report
from .store import FHIRStore

FHIR_JSON = "application/fhir+json"

SUPPORTED = {
    "Patient": ["_count"],
    "Encounter": ["subject", "patient", "status", "_count"],
    "Procedure": ["subject", "patient", "encounter", "code", "status", "_count"],
    "Device": ["_count"],
    "Observation": ["subject", "patient", "encounter", "code", "status", "_count"],
}

store = FHIRStore()
_report_cache: dict[str, Any] = {}


def _as_json(resource: Any) -> dict:
    """Serialize via the library's FHIR JSON writer, not `.dict()`.

    `.dict()` leaves native datetime/Decimal objects in place, which are not
    JSON-serializable and, more importantly, would not be FHIR-conformant even
    if they were — FHIR has its own primitive representations.
    """
    return json.loads(resource.model_dump_json())


def _outcome(severity: str, code: str, diagnostics: str) -> dict:
    oo = OperationOutcome(
        issue=[{"severity": severity, "code": code, "diagnostics": diagnostics}]
    )
    return _as_json(oo)


def _fhir(payload: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(content=payload, status_code=status, media_type=FHIR_JSON)


def create_app(
    n_cases: int = 25,
    seed: int = 42,
    kpi_db: Path | str | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Surgical Procedure FHIR Pipeline",
        description=(
            "Maps synthetic robotic-assisted surgery telemetry to FHIR R4B and "
            "exposes it over a conformant REST API."
        ),
        version="0.1.0",
    )

    cases = generate_cases(n=n_cases, seed=seed)
    report, resources = build_report(cases)
    store.load(resources)
    _report_cache["report"] = report

    kpi_store = KPIStore(db_path=kpi_db)
    kpi_store.persist(report)

    @app.get("/metadata")
    def metadata() -> JSONResponse:
        cs = CapabilityStatement(
            status="active",
            date="2026-07-14",
            kind="instance",
            fhirVersion="4.3.0",
            format=["json"],
            software={"name": "surgical-fhir-pipeline", "version": "0.1.0"},
            implementation={
                "description": "Synthetic RAS telemetry mapped to FHIR R4B"
            },
            rest=[
                {
                    "mode": "server",
                    "documentation": (
                        "Read-only. Write operations are intentionally not "
                        "implemented; this is a mapping demonstrator, not a "
                        "system of record."
                    ),
                    "resource": [
                        {
                            "type": rtype,
                            "interaction": [{"code": "read"}, {"code": "search-type"}],
                            "searchParam": [
                                {"name": p, "type": "token"} for p in params
                            ],
                        }
                        for rtype, params in SUPPORTED.items()
                    ],
                }
            ],
        )
        return _fhir(_as_json(cs))

    @app.get("/quality-report")
    def quality_report() -> JSONResponse:
        """Non-FHIR endpoint. The governance artefact, deliberately separate."""
        return JSONResponse(content=_report_cache["report"].to_dict())

    @app.get("/governance-kpis")
    def governance_kpis(limit: int = 50) -> JSONResponse:
        """Non-FHIR endpoint. Governance KPI trend across runs, oldest-first.

        Exposes the time-series of per-run quality metrics so a compliance
        reviewer or CIO can see exchangeability and trust-mix moving
        release-over-release, not just the latest snapshot.
        """
        runs = kpi_store.get_trend(limit=limit)
        return JSONResponse(
            content={
                "schema_version": "1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total_runs": len(runs),
                "runs": runs,
            }
        )

    @app.get("/{resource_type}/{rid}")
    def read(resource_type: str, rid: str) -> JSONResponse:
        if resource_type not in SUPPORTED:
            return _fhir(
                _outcome(
                    "error", "not-supported", f"Resource type {resource_type} not supported"
                ),
                404,
            )
        r = store.read(resource_type, rid)
        if r is None:
            return _fhir(
                _outcome("error", "not-found", f"{resource_type}/{rid} not found"), 404
            )
        return _fhir(_as_json(r))

    @app.get("/{resource_type}")
    def search(resource_type: str, request: Request) -> JSONResponse:
        if resource_type not in SUPPORTED:
            return _fhir(
                _outcome(
                    "error", "not-supported", f"Resource type {resource_type} not supported"
                ),
                404,
            )
        params = dict(request.query_params)
        try:
            results = store.search(resource_type, params)
        except ValueError as exc:
            return _fhir(_outcome("error", "not-supported", str(exc)), 400)

        bundle = Bundle(
            type="searchset",
            total=len(results),
            link=[BundleLink(relation="self", url=str(request.url))],
            entry=[
                BundleEntry(fullUrl=f"{request.base_url}{resource_type}/{r.id}", resource=r)
                for r in results
            ],
        )
        return _fhir(_as_json(bundle))

    return app


app = create_app()
