#!/usr/bin/env python3
"""Generate synthetic cases, map to FHIR, write bundles + quality report."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from surgical_fhir.generator import generate_cases
from surgical_fhir.kpi_store import KPIStore
from surgical_fhir.mapping import to_transaction_bundle
from surgical_fhir.quality import build_report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--cases", type=int, default=25)
    ap.add_argument("-s", "--seed", type=int, default=42)
    ap.add_argument("-o", "--out", default="output")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    cases = generate_cases(n=args.cases, seed=args.seed)
    report, resources = build_report(cases)

    run_id = KPIStore().persist(report)

    bundle = to_transaction_bundle(resources)
    (out / "transaction-bundle.json").write_text(bundle.model_dump_json(indent=2))
    (out / "quality-report.json").write_text(json.dumps(report.to_dict(), indent=2))
    (out / "quality-report.txt").write_text(report.render())

    print(report.render())
    print(f"\nWrote {len(resources)} resources -> {out}/transaction-bundle.json")
    print(f"KPI run persisted: {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
