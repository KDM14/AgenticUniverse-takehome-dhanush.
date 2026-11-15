import csv
import json
import os
import tempfile
import pytest
from pathlib import Path

from solution.db import Database
from solution.ingest import ingest_drop
from solution.report import generate_reports


def test_pipeline_incremental_and_idempotent(tmp_path):
    db_file = str(tmp_path / "test_pipeline.db")
    db = Database(db_file)

    # Ingest drop 1
    stats1 = ingest_drop("data/drop_1", db)
    assert stats1["lead_rows_total"] == 1515
    assert stats1["lead_rows_rejected"] == 71
    assert stats1["events_total"] == 4247

    # Re-run drop 1 (must be no-op)
    stats1_repeat = ingest_drop("data/drop_1", db)
    assert stats1_repeat["lead_rows_total"] == 0
    assert stats1_repeat["lead_rows_rejected"] == 0
    assert stats1_repeat["events_total"] == 0

    # Ingest drop 2
    stats2 = ingest_drop("data/drop_2", db)
    assert stats2["lead_rows_total"] == 542
    assert stats2["lead_rows_rejected"] == 37
    assert stats2["events_total"] == 4498

    # Generate reports
    out_dir = str(tmp_path / "reports")
    generate_reports(out_dir, db)

    # Verify reports exist
    out = Path(out_dir)
    assert (out / "batch_summary.csv").exists()
    assert (out / "daily_overview.csv").exists()
    assert (out / "lead_report.csv").exists()
    assert (out / "rejections.csv").exists()
    assert (out / "data_quality.json").exists()

    # Verify data_quality.json
    with open(out / "data_quality.json") as f:
        dq = json.load(f)
    assert dq["lead_rows_total"] == 2057
    assert dq["lead_rows_rejected"] == 108
    assert dq["duplicate_lead_rows_merged"] == 57
    assert dq["field_warnings"] == 122
    assert dq["events_total"] == 8745
    assert dq["malformed_events"] == 8
    assert dq["duplicate_events_dropped"] == 237
    assert dq["orphan_events"] == 462
    assert dq["unknown_disposition_events"] == 18

    # Verify no PII in rejections.csv
    with open(out / "rejections.csv") as f:
        reader = csv.reader(f)
        header = next(reader)
        assert header == ["workflow_id", "batch_id", "row_number", "reason"]
        for row in reader:
            assert len(row) == 4
            # Reason should not contain phone numbers or names
            assert not any(row[3].startswith(p) for p in ("+91", "91"))

    # Verify batch_summary sums
    with open(out / "batch_summary.csv") as f:
        reader = csv.DictReader(f)
        total_leads_all_batches = 0
        for row in reader:
            tot = int(row["total_leads"])
            y = int(row["yet_to_reach_out"])
            tf = int(row["in_progress_telephony_failed"])
            d = int(row["in_progress_dialed"])
            c = int(row["completed"])
            assert tot == y + tf + d + c
            total_leads_all_batches += tot
        assert total_leads_all_batches == 1892  # 2057 total rows - 108 rejected - 57 merged dups
