"""
Database initialization and connection management using SQLite.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS leads (
    workflow_id TEXT NOT NULL,
    phone_hash TEXT NOT NULL,
    phone_masked TEXT NOT NULL,
    phone_enc TEXT NOT NULL,
    customer_name_enc TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workflow_id, phone_hash)
);

CREATE TABLE IF NOT EXISTS rejections (
    workflow_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (workflow_id, batch_id, row_number)
);

CREATE TABLE IF NOT EXISTS events_ledger (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    occurred_date TEXT NOT NULL,
    phone_hash TEXT,
    raw_phone TEXT,
    disposition_code TEXT,
    is_valid_disposition INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    drop_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_call ON events_ledger(call_id);
CREATE INDEX IF NOT EXISTS idx_events_wf_phone ON events_ledger(workflow_id, phone_hash);
CREATE INDEX IF NOT EXISTS idx_events_type ON events_ledger(event_type);
CREATE INDEX IF NOT EXISTS idx_leads_wf_batch ON leads(workflow_id, batch_id);
CREATE INDEX IF NOT EXISTS idx_leads_wf_ref ON leads(workflow_id, external_ref);

CREATE TABLE IF NOT EXISTS ingested_files (
    file_path TEXT PRIMARY KEY,
    drop_id TEXT NOT NULL,
    file_type TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_counters (
    metric_name TEXT PRIMARY KEY,
    metric_value INTEGER NOT NULL DEFAULT 0
);
"""

class Database:
    def __init__(self, db_path: str = "pipeline.db"):
        self.db_path = db_path
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)

    def is_file_ingested(self, file_path: str) -> bool:
        with self.get_connection() as conn:
            cur = conn.execute("SELECT 1 FROM ingested_files WHERE file_path = ?", (file_path,))
            return cur.fetchone() is not None

    def record_ingested_file(self, file_path: str, drop_id: str, file_type: str, timestamp: str):
        with self.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ingested_files (file_path, drop_id, file_type, ingested_at) VALUES (?, ?, ?, ?)",
                (file_path, drop_id, file_type, timestamp),
            )

    def increment_counters(self, deltas: Dict[str, int]):
        with self.get_connection() as conn:
            for k, v in deltas.items():
                if v != 0:
                    conn.execute(
                        """
                        INSERT INTO pipeline_counters (metric_name, metric_value)
                        VALUES (?, ?)
                        ON CONFLICT(metric_name) DO UPDATE SET metric_value = metric_value + ?
                        """,
                        (k, v, v),
                    )

    def get_counters(self) -> Dict[str, int]:
        with self.get_connection() as conn:
            cur = conn.execute("SELECT metric_name, metric_value FROM pipeline_counters")
            return {row["metric_name"]: row["metric_value"] for row in cur.fetchall()}
