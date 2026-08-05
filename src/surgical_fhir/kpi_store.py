"""Governance KPI ledger.

Persists per-run quality metrics to a local SQLite database so a compliance
reviewer can see exchangeability and trust-mix trends across runs — not just
the latest snapshot.

SQLite is the right call here: durable, queryable, zero-infra, ships with
Python. The DB path is configurable via SURGICAL_FHIR_KPI_DB so CI and
production can point to different files without code changes.

Schema notes:
- Scalar trend metrics (exchangeable_rate, trust counts) are flat columns
  because they are always queried and always present.
- drop_reasons and issues_by_severity are JSON because their keys vary by
  run; new categories appear without a schema migration.
- Adding a new scalar metric later: ALTER TABLE ADD COLUMN ... DEFAULT NULL.
  SQLite supports this with no data loss and no table rebuild.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .quality import QualityReport

_DEFAULT_DB = Path(os.getenv("SURGICAL_FHIR_KPI_DB", "governance_kpis.db"))

_DDL = """
CREATE TABLE IF NOT EXISTS governance_runs (
    run_id             TEXT    PRIMARY KEY,
    timestamp          TEXT    NOT NULL,
    n_cases            INTEGER NOT NULL,
    n_exchangeable     INTEGER NOT NULL,
    n_dropped          INTEGER NOT NULL,
    exchangeable_rate  REAL    NOT NULL,
    trust_verified     INTEGER NOT NULL,
    trust_provisional  INTEGER NOT NULL,
    trust_total        INTEGER NOT NULL,
    drop_reasons       TEXT    NOT NULL,
    issues_by_severity TEXT    NOT NULL
);
"""

_INSERT = """
INSERT OR REPLACE INTO governance_runs
    (run_id, timestamp, n_cases, n_exchangeable, n_dropped,
     exchangeable_rate, trust_verified, trust_provisional,
     trust_total, drop_reasons, issues_by_severity)
VALUES (?,?,?,?,?,?,?,?,?,?,?)
"""

_SELECT_TREND = """
SELECT * FROM governance_runs
ORDER BY timestamp ASC, run_id ASC
LIMIT ?
"""


class KPIStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db = Path(db_path) if db_path is not None else _DEFAULT_DB
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_DDL)

    def persist(self, report: QualityReport, run_id: str | None = None) -> str:
        """Write one run's metrics to the ledger. Returns the run_id used."""
        rid = run_id or _new_run_id()
        tc = report.terminology_coverage
        row = (
            rid,
            datetime.now(timezone.utc).isoformat(),
            report.cases_in,
            report.cases_exchangeable,
            report.cases_dropped,
            round(report.exchangeable_rate, 6),
            tc.get("verified", 0),
            tc.get("provisional", 0),
            tc.get("total", 0),
            json.dumps(report.error_by_element),
            json.dumps(report.issues_by_severity),
        )
        with self._connect() as conn:
            conn.execute(_INSERT, row)
        return rid

    def get_trend(self, limit: int = 50) -> list[dict]:
        """Return runs oldest-first — natural direction for a trend chart."""
        with self._connect() as conn:
            rows = conn.execute(_SELECT_TREND, (limit,)).fetchall()
        return [_row_to_dict(r) for r in rows]


def _new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"run-{ts}-{uuid.uuid4().hex[:6]}"


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "run_id": row["run_id"],
        "timestamp": row["timestamp"],
        "n_cases": row["n_cases"],
        "n_exchangeable": row["n_exchangeable"],
        "n_dropped": row["n_dropped"],
        "exchangeable_rate": row["exchangeable_rate"],
        "trust_mix": {
            "verified": row["trust_verified"],
            "provisional": row["trust_provisional"],
            "total": row["trust_total"],
        },
        "drop_reasons": json.loads(row["drop_reasons"]),
        "issues_by_severity": json.loads(row["issues_by_severity"]),
    }
