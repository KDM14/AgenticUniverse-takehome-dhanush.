"""
Report generation pipeline producing the 5 contract output files:
1. batch_summary.csv
2. daily_overview.csv
3. lead_report.csv
4. rejections.csv
5. data_quality.json
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from solution.db import Database
from solution.schema import SchemaRegistry, parse_timestamp


def generate_reports(out_dir: str, db: Database, registry: Optional[SchemaRegistry] = None):
    """
    Generate all 5 contract reports and write them to out_dir.
    """
    if registry is None:
        registry = SchemaRegistry()

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    with db.get_connection() as conn:
        # 1. Load all valid leads
        cur = conn.execute(
            """
            SELECT workflow_id, phone_hash, phone_masked, batch_id, external_ref, data_json
            FROM leads
            ORDER BY workflow_id, external_ref
            """
        )
        leads_list = [dict(row) for row in cur.fetchall()]
        leads_by_ident = {(l["workflow_id"], l["phone_hash"]): l for l in leads_list}

        # 2. Load all events from ledger
        cur = conn.execute(
            """
            SELECT event_id, event_type, workflow_id, call_id, occurred_at, occurred_date,
                   phone_hash, raw_phone, disposition_code, is_valid_disposition, payload_json
            FROM events_ledger
            ORDER BY occurred_at ASC
            """
        )
        all_events = [dict(row) for row in cur.fetchall()]

        # 3. Determine call -> lead mapping
        # First pass: map call_id -> (workflow_id, phone_hash) from events having phone_hash
        call_to_lead: Dict[str, Tuple[str, str]] = {}
        for e in all_events:
            wf = e["workflow_id"]
            ph = e["phone_hash"]
            cid = e["call_id"]
            if ph and (wf, ph) in leads_by_ident:
                call_to_lead[cid] = (wf, ph)

        # 4. Classify orphan and unknown disposition events
        orphan_events_count = 0
        unknown_disposition_count = 0

        # Group events by call_id for call-level facts
        events_by_call = defaultdict(list)

        for e in all_events:
            wf = e["workflow_id"]
            cid = e["call_id"]
            ph = e["phone_hash"]
            etype = e["event_type"]

            lead_ident = None
            if ph and (wf, ph) in leads_by_ident:
                lead_ident = (wf, ph)
            elif etype == "disposition.override":
                lead_ident = call_to_lead.get(cid)

            if not lead_ident:
                # Event cannot be linked to any valid lead
                orphan_events_count += 1
            else:
                # Event is linked
                events_by_call[cid].append(e)
                if etype.startswith("disposition."):
                    if e["is_valid_disposition"] == 0:
                        unknown_disposition_count += 1

        # 5. Derive call facts
        # "A call is counted only if its call.initiated event is present (and linked)."
        calls = {}
        for cid, evts in events_by_call.items():
            init_events = [e for e in evts if e["event_type"] == "call.initiated"]
            if not init_events:
                continue

            # Earliest initiated event defines the call start
            init_e = sorted(init_events, key=lambda x: x["occurred_at"])[0]
            lead_ident = call_to_lead[cid]
            wf = init_e["workflow_id"]
            init_ts_str = init_e["occurred_at"]
            init_date_str = init_e["occurred_date"]

            has_connected = any(e["event_type"] == "call.connected" for e in evts)
            has_telephony_failed = any(e["event_type"] == "call.telephony_failed" for e in evts)

            # Effective disposition:
            # Code of latest disposition.override by occurred_at, else latest disposition.set
            valid_overrides = [
                e for e in evts
                if e["event_type"] == "disposition.override" and e["is_valid_disposition"] == 1
            ]
            valid_sets = [
                e for e in evts
                if e["event_type"] == "disposition.set" and e["is_valid_disposition"] == 1
            ]

            eff_code = None
            if valid_overrides:
                latest_override = sorted(valid_overrides, key=lambda x: x["occurred_at"])[-1]
                eff_code = latest_override["disposition_code"]
            elif valid_sets:
                latest_set = sorted(valid_sets, key=lambda x: x["occurred_at"])[-1]
                eff_code = latest_set["disposition_code"]

            disp_meta = registry.dispositions.get((wf, eff_code), {}) if eff_code else {}
            is_terminal = disp_meta.get("is_terminal", False)
            is_success = disp_meta.get("is_success", False)

            calls[cid] = {
                "call_id": cid,
                "workflow_id": wf,
                "lead_ident": lead_ident,
                "init_at": init_ts_str,
                "init_date": init_date_str,
                "connected": has_connected,
                "telephony_failed": has_telephony_failed,
                "effective_disposition": eff_code,
                "is_terminal": is_terminal,
                "is_success": is_success,
            }

        # 6. Group calls by lead
        calls_by_lead = defaultdict(list)
        for c in calls.values():
            calls_by_lead[c["lead_ident"]].append(c)

        # 7. Compute lead metrics and buckets
        # Bucket precedence:
        # 1. yet_to_reach_out: no calls
        # 2. completed: any call's effective disposition is terminal
        # 3. in_progress_telephony_failed: every call has a call.telephony_failed event
        # 4. in_progress_dialed: everything else
        lead_metrics = {}
        for ident, lead in leads_by_ident.items():
            cs = calls_by_lead.get(ident, [])
            total_calls = len(cs)
            connected_calls = sum(1 for c in cs if c["connected"])

            if total_calls == 0:
                bucket = "yet_to_reach_out"
                last_call_at = ""
                current_disposition = ""
                ever_success = "false"
            else:
                # Bucket calculation
                if any(c["is_terminal"] for c in cs):
                    bucket = "completed"
                elif all(c["telephony_failed"] for c in cs):
                    bucket = "in_progress_telephony_failed"
                else:
                    bucket = "in_progress_dialed"

                # Latest call.initiated time
                latest_call = sorted(cs, key=lambda x: x["init_at"])[-1]
                last_call_at = latest_call["init_at"]

                # Current disposition:
                # "the effective disposition of the most recent call (by initiated time) that has one. Empty if none."
                calls_with_disp = [c for c in cs if c["effective_disposition"]]
                if calls_with_disp:
                    most_recent_disp_call = sorted(calls_with_disp, key=lambda x: x["init_at"])[-1]
                    current_disposition = most_recent_disp_call["effective_disposition"]
                else:
                    current_disposition = ""

                ever_success = "true" if any(c["is_success"] for c in cs) else "false"

            lead_metrics[ident] = {
                "workflow_id": lead["workflow_id"],
                "external_ref": lead["external_ref"],
                "batch_id": lead["batch_id"],
                "phone_masked": lead["phone_masked"],
                "bucket": bucket,
                "total_calls": total_calls,
                "connected_calls": connected_calls,
                "last_call_at": last_call_at,
                "current_disposition": current_disposition,
                "ever_success": ever_success,
            }

        # Write Report 1: batch_summary.csv
        # Sorted by workflow_id, batch_id
        batch_counts = defaultdict(lambda: {
            "total_leads": 0,
            "yet_to_reach_out": 0,
            "in_progress_telephony_failed": 0,
            "in_progress_dialed": 0,
            "completed": 0,
        })
        for ident, lm in lead_metrics.items():
            key = (lm["workflow_id"], lm["batch_id"])
            batch_counts[key]["total_leads"] += 1
            batch_counts[key][lm["bucket"]] += 1

        batch_summary_file = out_path / "batch_summary.csv"
        with open(batch_summary_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "workflow_id", "batch_id", "total_leads", "yet_to_reach_out",
                "in_progress_telephony_failed", "in_progress_dialed", "completed"
            ])
            for (wf, b_id) in sorted(batch_counts.keys()):
                cnts = batch_counts[(wf, b_id)]
                writer.writerow([
                    wf,
                    b_id,
                    cnts["total_leads"],
                    cnts["yet_to_reach_out"],
                    cnts["in_progress_telephony_failed"],
                    cnts["in_progress_dialed"],
                    cnts["completed"],
                ])

        # Write Report 2: daily_overview.csv
        # One row per workflow per IST date that has at least one call, sorted by workflow_id, date
        daily_stats = defaultdict(lambda: {
            "total_calls": 0,
            "connected_calls": 0,
            "success_leads": set(),
        })
        for c in calls.values():
            key = (c["workflow_id"], c["init_date"])
            daily_stats[key]["total_calls"] += 1
            if c["connected"]:
                daily_stats[key]["connected_calls"] += 1
            if c["is_success"]:
                daily_stats[key]["success_leads"].add(c["lead_ident"])

        daily_overview_file = out_path / "daily_overview.csv"
        with open(daily_overview_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"
            ])
            for (wf, dt) in sorted(daily_stats.keys()):
                ds = daily_stats[(wf, dt)]
                tot = ds["total_calls"]
                conn_cnt = ds["connected_calls"]
                rate = f"{conn_cnt / tot:.4f}" if tot > 0 else "0.0000"
                writer.writerow([
                    wf,
                    dt,
                    tot,
                    conn_cnt,
                    rate,
                    len(ds["success_leads"]),
                ])

        # Write Report 3: lead_report.csv
        # Sorted by workflow_id, external_ref
        sorted_lead_rows = sorted(
            lead_metrics.values(),
            key=lambda x: (x["workflow_id"], x["external_ref"])
        )
        lead_report_file = out_path / "lead_report.csv"
        with open(lead_report_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "workflow_id", "external_ref", "batch_id", "phone_masked", "bucket",
                "total_calls", "connected_calls", "last_call_at", "current_disposition", "ever_success"
            ])
            for lr in sorted_lead_rows:
                writer.writerow([
                    lr["workflow_id"],
                    lr["external_ref"],
                    lr["batch_id"],
                    lr["phone_masked"],
                    lr["bucket"],
                    lr["total_calls"],
                    lr["connected_calls"],
                    lr["last_call_at"],
                    lr["current_disposition"],
                    lr["ever_success"],
                ])

        # Write Report 4: rejections.csv
        # Sorted by workflow_id, batch_id, row_number
        cur = conn.execute(
            """
            SELECT workflow_id, batch_id, row_number, reason
            FROM rejections
            ORDER BY workflow_id, batch_id, row_number ASC
            """
        )
        rejection_rows = cur.fetchall()
        rejections_file = out_path / "rejections.csv"
        with open(rejections_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["workflow_id", "batch_id", "row_number", "reason"])
            for row in rejection_rows:
                writer.writerow([row["workflow_id"], row["batch_id"], row["row_number"], row["reason"]])

        # Write Report 5: data_quality.json
        cur = conn.execute("SELECT reason, COUNT(*) as cnt FROM rejections GROUP BY reason ORDER BY reason")
        rejections_by_reason = {row["reason"]: row["cnt"] for row in cur.fetchall()}

        counters = db.get_counters()
        data_quality = {
            "lead_rows_total": counters.get("lead_rows_total", 0),
            "lead_rows_rejected": counters.get("lead_rows_rejected", 0),
            "rejections_by_reason": rejections_by_reason,
            "duplicate_lead_rows_merged": counters.get("duplicate_lead_rows_merged", 0),
            "field_warnings": counters.get("field_warnings", 0),
            "events_total": counters.get("events_total", 0),
            "malformed_events": counters.get("malformed_events", 0),
            "duplicate_events_dropped": counters.get("duplicate_events_dropped", 0),
            "orphan_events": orphan_events_count,
            "unknown_disposition_events": unknown_disposition_count,
        }

        data_quality_file = out_path / "data_quality.json"
        with open(data_quality_file, "w", encoding="utf-8") as f:
            json.dump(data_quality, f, indent=2)

    print(f"Generated 5 report files into {out_dir}/ successfully.")
