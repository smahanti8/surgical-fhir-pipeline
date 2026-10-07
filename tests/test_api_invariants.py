"""Referential-integrity guard in create_app().

Regression test: create_app() looked up each case's Encounter via
store.read() and dereferenced it without a None check. Every Procedure is
supposed to have a matching Encounter (see mapping.py and
test_referential_integrity_no_orphan_resources in test_pipeline.py), but
api.py had no guard of its own — a future violation of that invariant would
have surfaced as an unhandled AttributeError instead of a clear error.
"""
from __future__ import annotations

import pytest

import surgical_fhir.api as api_module
from surgical_fhir.quality import build_report as real_build_report
from surgical_fhir.store import FHIRStore


def test_create_app_raises_clear_error_when_encounter_missing_for_procedure(monkeypatch):
    def build_report_without_encounters(cases):
        report, resources = real_build_report(cases)
        resources = [r for r in resources if r.__resource_type__ != "Encounter"]
        return report, resources

    # api.py's module-level `store` is a singleton already populated by the
    # `app = create_app()` at import time; swap in a fresh one so stale
    # Encounters loaded before this test can't mask the ones removed above.
    monkeypatch.setattr(api_module, "store", FHIRStore())
    monkeypatch.setattr(api_module, "build_report", build_report_without_encounters)

    with pytest.raises(RuntimeError, match="referential integrity invariant violated"):
        api_module.create_app(n_cases=5)
