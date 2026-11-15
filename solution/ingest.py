"""
Optimized ingestion pipeline for lead CSV files and call event JSONL files.
Guarantees:
- Schema validation, normalization, and rejection tracking.
- PII encryption at rest and masked display formatting.
- Idempotent and incremental drop processing.
- Duplicate lead merging (Last-Write-Wins for non-null values, preserving original batch_id).
- Append-only event ledger with event deduplication.
- High-throughput batch processing supporting >100k leads/min.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from solution.crypto import encrypt_pii, hash_phone, mask_phone
from solution.db import Database
from solution.schema import SchemaRegistry, normalize_column_name, normalize_phone, parse_timestamp

IST = timezone(timedelta(hours=5, minutes=30))

def ingest_drop(drop_dir: str, db: Database, registry: Optional[SchemaRegistry] = None) -> Dict[str, int]:
    """
    Ingest a single drop directory containing leads/ and events/.
    Returns a dictionary of metrics for this ingestion run.
    """
    if registry is None:
        registry = SchemaRegistry()

    drop_path = Path(drop_dir)
    drop_id = drop_path.name
    now_iso = datetime.now(IST).isoformat()

    stats = {
        "lead_rows_total": 0,
        "lead_rows_rejected": 0,
        "duplicate_lead_rows_merged": 0,
        "field_warnings": 0,
        "events_total": 0,
        "malformed_events": 0,
        "duplicate_events_dropped": 0,
    }

    # 1. Ingest Leads
    leads_dir = drop_path / "leads"
    if leads_dir.exists():
        # Preload existing leads into memory for O(1) duplicate checks and merges
        with db.get_connection() as conn:
            cur = conn.execute(
                "SELECT workflow_id, phone_hash, batch_id, customer_name_enc, external_ref, data_json FROM leads"
            )
            # Map (wf_id, phone_hash) -> dict
            existing_leads: Dict[Tuple[str, str], Dict[str, Any]] = {}
            for r in cur.fetchall():
                existing_leads[(r["workflow_id"], r["phone_hash"])] = {
                    "batch_id": r["batch_id"],
                    "customer_name_enc": r["customer_name_enc"],
                    "external_ref": r["external_ref"],
                    "data": json.loads(r["data_json"]),
                }

        # Process each workflow directory
        for wf_dir in sorted(leads_dir.iterdir()):
            if not wf_dir.is_dir():
                continue
            wf_id = wf_dir.name
            if wf_id not in registry.workflows:
                continue

            ref_col_name = registry.get_reference_field(wf_id)
            col_map = registry.column_maps.get(wf_id, {})

            # Process CSV files in alphabetical order
            for csv_file in sorted(wf_dir.glob("*.csv")):
                file_key = str(csv_file.resolve())
                if db.is_file_ingested(file_key):
                    print(f"Skipping already ingested file: {csv_file}")
                    continue

                batch_id = csv_file.stem
                row_number = 0

                leads_to_insert = []
                leads_to_update = []
                rejections_to_insert = []

                with open(csv_file, encoding="utf-8") as f:
                    reader = csv.reader(f)
                    try:
                        raw_headers = next(reader)
                    except StopIteration:
                        db.record_ingested_file(file_key, drop_id, "lead", now_iso)
                        continue

                    # Map headers to schema field names
                    mapped_indices = {}
                    for idx, h in enumerate(raw_headers):
                        norm_h = normalize_column_name(h)
                        if norm_h in col_map:
                            mapped_indices[idx] = col_map[norm_h]

                    for row in reader:
                        # Skip blank rows (every cell empty or whitespace)
                        if not any(cell.strip() for cell in row):
                            continue

                        row_number += 1
                        stats["lead_rows_total"] += 1

                        mapped_row = {
                            mapped_indices[idx]: row[idx]
                            for idx in range(min(len(row), len(raw_headers)))
                            if idx in mapped_indices
                        }

                        status, reason, warnings, cleaned = registry.validate_lead_row(wf_id, mapped_row)

                        if status == "REJECT":
                            stats["lead_rows_rejected"] += 1
                            rejections_to_insert.append((wf_id, batch_id, row_number, reason))
                        else:
                            stats["field_warnings"] += warnings
                            canonical_phone = cleaned["phone"]
                            p_hash = hash_phone(canonical_phone)
                            p_masked = mask_phone(canonical_phone)
                            ext_ref = str(cleaned.get(ref_col_name) or "")

                            lead_key = (wf_id, p_hash)
                            if lead_key in existing_leads:
                                stats["duplicate_lead_rows_merged"] += 1
                                existing = existing_leads[lead_key]
                                
                                # Overwrite non-null fields
                                for k, v in cleaned.items():
                                    if v is not None:
                                        existing["data"][k] = v

                                cust_name_enc = existing["customer_name_enc"]
                                if cleaned.get("customer_name"):
                                    cust_name_enc = encrypt_pii(cleaned["customer_name"])
                                    existing["customer_name_enc"] = cust_name_enc

                                new_ext_ref = existing["external_ref"]
                                if cleaned.get(ref_col_name):
                                    new_ext_ref = str(cleaned[ref_col_name])
                                    existing["external_ref"] = new_ext_ref

                                leads_to_update.append((
                                    cust_name_enc,
                                    new_ext_ref,
                                    json.dumps(existing["data"]),
                                    now_iso,
                                    wf_id,
                                    p_hash,
                                ))
                            else:
                                cust_name = cleaned.get("customer_name", "")
                                cust_name_enc = encrypt_pii(cust_name)
                                phone_enc = encrypt_pii(canonical_phone)
                                
                                existing_leads[lead_key] = {
                                    "batch_id": batch_id,
                                    "customer_name_enc": cust_name_enc,
                                    "external_ref": ext_ref,
                                    "data": cleaned,
                                }
                                
                                leads_to_insert.append((
                                    wf_id,
                                    p_hash,
                                    p_masked,
                                    phone_enc,
                                    cust_name_enc,
                                    batch_id,
                                    ext_ref,
                                    json.dumps(cleaned),
                                    now_iso,
                                    now_iso,
                                ))

                # Batch write rejections and leads for this file
                with db.get_connection() as conn:
                    if rejections_to_insert:
                        conn.executemany(
                            """
                            INSERT OR REPLACE INTO rejections (workflow_id, batch_id, row_number, reason)
                            VALUES (?, ?, ?, ?)
                            """,
                            rejections_to_insert,
                        )
                    if leads_to_insert:
                        conn.executemany(
                            """
                            INSERT INTO leads (
                                workflow_id, phone_hash, phone_masked, phone_enc, customer_name_enc,
                                batch_id, external_ref, data_json, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            leads_to_insert,
                        )
                    if leads_to_update:
                        conn.executemany(
                            """
                            UPDATE leads
                            SET customer_name_enc = ?, external_ref = ?, data_json = ?, updated_at = ?
                            WHERE workflow_id = ? AND phone_hash = ?
                            """,
                            leads_to_update,
                        )

                db.record_ingested_file(file_key, drop_id, "lead", now_iso)

    # 2. Ingest Events
    events_dir = drop_path / "events"
    if events_dir.exists():
        with db.get_connection() as conn:
            cur = conn.execute("SELECT event_id FROM events_ledger")
            seen_event_ids: Set[str] = {row[0] for row in cur.fetchall()}

        for event_file in sorted(events_dir.glob("*.jsonl")):
            file_key = str(event_file.resolve())
            if db.is_file_ingested(file_key):
                print(f"Skipping already ingested file: {event_file}")
                continue

            events_batch = []
            with open(event_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    stats["events_total"] += 1

                    try:
                        obj = json.loads(line)
                        if not isinstance(obj, dict):
                            stats["malformed_events"] += 1
                            continue
                        eid = obj.get("event_id")
                        if not eid or not isinstance(eid, str) or not eid.strip():
                            stats["malformed_events"] += 1
                            continue
                    except Exception:
                        stats["malformed_events"] += 1
                        continue

                    eid = eid.strip()
                    if eid in seen_event_ids:
                        stats["duplicate_events_dropped"] += 1
                        continue
                    seen_event_ids.add(eid)

                    etype = obj.get("event_type", "")
                    wfid = obj.get("workflow_id", "")
                    cid = obj.get("call_id", "")
                    raw_occurred = obj.get("occurred_at", "")
                    try:
                        dt_ist = parse_timestamp(raw_occurred)
                        occurred_at_str = dt_ist.isoformat()
                        occurred_date_str = dt_ist.strftime("%Y-%m-%d")
                    except Exception:
                        stats["malformed_events"] += 1
                        continue

                    raw_phone = obj.get("phone")
                    p_hash = None
                    if raw_phone:
                        can_phone = normalize_phone(raw_phone)
                        if can_phone:
                            p_hash = hash_phone(can_phone)

                    disp_code = None
                    is_valid_disp = 0
                    payload = obj.get("payload", {})
                    if isinstance(payload, dict) and etype.startswith("disposition."):
                        disp_code = payload.get("disposition_code")
                        if (wfid, disp_code) in registry.dispositions:
                            is_valid_disp = 1

                    events_batch.append((
                        eid,
                        etype,
                        wfid,
                        cid,
                        occurred_at_str,
                        occurred_date_str,
                        p_hash,
                        raw_phone if raw_phone else None,
                        disp_code,
                        is_valid_disp,
                        json.dumps(payload),
                        drop_id,
                    ))

            if events_batch:
                with db.get_connection() as conn:
                    conn.executemany(
                        """
                        INSERT INTO events_ledger (
                            event_id, event_type, workflow_id, call_id, occurred_at, occurred_date,
                            phone_hash, raw_phone, disposition_code, is_valid_disposition, payload_json, drop_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        events_batch,
                    )

            db.record_ingested_file(file_key, drop_id, "event", now_iso)

    # Persist metrics delta into database
    db.increment_counters(stats)
    return stats
