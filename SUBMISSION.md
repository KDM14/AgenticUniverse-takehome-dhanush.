# Submission

## Candidate
- **Name:** Dhanush Manikanta
- **Assignment chosen:** A (Data & Interaction Ledger)
- **Time spent (honest estimate):** 6h

## How to run
```bash
# 1. Environment setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Ingest drops incrementally
python3 -m solution.cli ingest data/drop_1
python3 -m solution.cli ingest data/drop_2

# 3. Generate the 5 contract reports
python3 -m solution.cli report --out reports/

# 4. Run test suite
pytest
```

## Design

### Architecture Overview
The solution is designed around an append-only event ledger and an idempotent lead processing engine built on SQLite in WAL mode. It separates raw ingestion from analytical derivation, ensuring that raw incoming events and leads are validated, protected, and stored safely before computing business reports.

```
                    ┌─────────────────────────┐
                    │  Drop Files (CSV / JSONL)│
                    └───────────┬─────────────┘
                                │
                      [ Ingestion Pipeline ]
                                │
        ┌───────────────────────┴───────────────────────┐
        ▼                                               ▼
┌─────────────────────────┐                  ┌─────────────────────────┐
│     Leads Table         │                  │   Events Ledger Table   │
│  - (wf_id, phone_hash)  │                  │  - Append-only          │
│  - Fernet encrypted PII │                  │  - Deduplicated event_id│
│  - Permanent batch_id   │                  │  - Normalized IST dates │
│  - LWW non-null merge   │                  │  - Disposition catalog  │
└───────────┬─────────────┘                  └───────────┬─────────────┘
            │                                            │
            └───────────────────┬────────────────────────┘
                                │
                      [ Reporting Engine ]
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
 1. batch_summary.csv    2. daily_overview.csv   3. lead_report.csv
 4. rejections.csv       5. data_quality.json
```

### Database Schema
1. **`leads`**:
   - `(workflow_id, phone_hash)` PRIMARY KEY: `phone_hash` is the deterministic SHA-256 hash of the canonical phone `+91XXXXXXXXXX`. This enables fast $O(1)$ joins with call events without storing raw phone numbers in plaintext.
   - `phone_masked`: `+91******XXXX` formatted phone for reports.
   - `phone_enc` & `customer_name_enc`: Encrypted at rest using Fernet (AES-128-CBC with HMAC authentication).
   - `batch_id`: Permanently records the file stem of the lead's first valid row.
   - `external_ref`: Extracted from the workflow schema's reference column (`policy_number` or `order_id`).
   - `data_json`: JSON-serialized attributes updated via Last-Write-Wins on non-null fields.
2. **`rejections`**:
   - `(workflow_id, batch_id, row_number)` PRIMARY KEY: Stores rejected rows with the exact failure reason. Contains strictly zero PII.
3. **`events_ledger`**:
   - `event_id` PRIMARY KEY: Enforces at-most-once storage per event.
   - `workflow_id`, `call_id`, `event_type`, `occurred_at`, `occurred_date`: Stores parsed IST timestamps.
   - `phone_hash`, `raw_phone`: Phone hash for linking to leads.
   - `disposition_code`, `is_valid_disposition`: Pre-validated against the disposition catalogue.
4. **`ingested_files`**:
   - Tracks processed file paths and ingestion timestamps to ensure idempotent re-runs.
5. **`pipeline_counters`**:
   - Stores cumulative ingestion counters across drops.

## Decisions & Assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| **Zero-Knowledge Phone Joining (`phone_hash`)** | The spec mandates PII protection at rest while requiring phone-based linking between events and leads. Storing deterministic SHA-256 hashes of canonical phones allows indexed queries without plaintext exposure. | Storing plaintext phone or decrypting every row in memory during joins. |
| **Fernet AES Authenticated Encryption for PII** | Guarantees confidentiality and integrity of customer names and phone numbers stored in the database. | Plaintext storage (violates spec) or hashing names (irreversible). |
| **In-Memory Hash Map with Batched DB Commits** | Ingestion of `--scale large` (144k leads, 632k events) finishes in ~43 seconds because lead lookups are $O(1)$ in RAM before batch-committing via `executemany`. | Row-by-row `SELECT` queries in SQLite (~15 minutes on large dataset). |
| **Schema Walk Rejection & Warning Precedence** | The spec requires checking fields in schema order. If an optional field is invalid but a subsequent required field fails, the row is rejected and the optional warning is discarded, strictly respecting "count warnings only for rows that are not rejected". | Accumulating warnings before rejecting rows. |
| **Trailing Underscore Normalization** | Applied `re.sub(r"[\s_]+", " ", name.lower()).strip()` to ensure trailing underscores or spaces (e.g. `Policy__Number_`) match alias mappings accurately. | Simple `.strip().lower()` before regex replacement. |
| **Orphan Event Dynamic Reconciliation** | Storing all valid non-duplicate events into the ledger allows events arriving in `drop_1` to dynamically link to leads that arrive in `drop_2`. Orphan counts in `data_quality.json` reflect the final state as specified. | Dropping unlinked events on arrival. |
| **Unknown Disposition Precedence** | Step 3 (Orphan) precedes Step 4 (Unknown disposition) in the spec. Events that cannot link to a valid lead are categorized as orphans first. Linked events with uncatalogued codes are counted as unknown dispositions. | Counting unknown dispositions regardless of lead linkage. |
| **Call Counting & Connectivity Rate** | A call is counted only when `call.initiated` is present and linked. Connectivity rate is formatted to 4 decimals (`0.5625`), matching the exact spec example. | Counting any event with a `call_id` as a call. |

## Failure modes considered
- **Re-running Ingestion (Idempotency):** Tracked in `ingested_files`. Re-running `ingest data/drop_1` detects already-processed files, issues a skip notice, adds 0 to pipeline metrics, and leaves reports identical (byte-for-byte verified via SHA-256).
- **Malformed Event Lines:** Empty lines are skipped and not counted. Lines with invalid JSON or missing `event_id` are recorded in `malformed_events` without halting the pipeline.
- **Duplicate Events Across Drops:** Deduplicated by `event_id` across all drops and recorded in `duplicate_events_dropped`.
- **Out-of-Order Call Events & Overrides:** All events for a call are indexed by `occurred_at`. The latest valid `disposition.override` always supersedes `disposition.set` regardless of which drop or order they arrived in.
- **Crash & Transaction Safety:** SQLite WAL mode and atomic transactions ensure that crashes between files do not leave partial or corrupt writes.
- **Unlinkable Overrides:** If a supervisor override arrives without other call events, it is flagged as an orphan and omitted from reports until matching call events link it.

## Testing
- **Tested:**
  - Phone normalisation across 6 input variants (`+91`, `0`, `91-`, parens, spaces), invalid prefixes (e.g. 5, landlines, international, short/long).
  - Date parsing across ISO, DD/MM/YYYY, DD-MM-YYYY, DD-Mon-YYYY (case-insensitive), impossible dates (31/02, 2026-13-01, non-leap years).
  - Event timestamp parsing across UTC (`Z`), explicit offset (`+05:30`), and naive IST.
  - Header normalization and alias matching.
  - Schema order rejection precedence and field warning isolation.
  - Derived facts: effective disposition resolution, call connectivity, bucket precedence (yet_to_reach_out > completed > in_progress_telephony_failed > in_progress_dialed).
  - End-to-end multi-drop pipeline run with idempotency and orphan reconciliation.
- **Test execution command:** `pytest -v` (15 passing tests in 0.29s).

## Results
- **Timings on synthetic datasets (Apple Silicon M-series):**
  - **Small dataset (`--scale small`):**
    - `drop_1` ingest (1,515 leads, 4,247 events): **0.42s**
    - `drop_2` ingest (542 leads, 4,498 events): **0.38s**
    - Report generation: **0.15s**
  - **Large dataset (`--scale large`, 143,990 leads, 632,602 events):**
    - `drop_1` ingest: **16.03s**
    - `drop_2` ingest: **23.46s**
    - `report --out reports_large`: **4.21s**
    - **Total runtime:** **43.7 seconds** (comfortably under the 2-minute stretch threshold).
- **Data Insights & Observations:**
  - Out of 2,057 total non-blank lead rows in seed 42, exactly 108 were rejected (42 invalid phones, 66 missing required fields) and 57 duplicate rows were merged, leaving exactly 1,892 unique leads.
  - Exactly 462 events were orphans (due to rejected leads and un-uploaded numbers) and 18 events had uncatalogued dispositions (`RENEWD`, `call_back`, `unknown`), matching the synthetic generator's defect parameters.

## If I had another day
1. **Automated Schema Migrations & Multi-tenant Config:** Implement dynamic schema loader supporting versioned JSON schemas fetched from an object store (e.g. S3 / GCS).
2. **Streaming Event Ingestion (Kafka / SQS):** Replace file batching with a continuous consumer feeding directly into the append-only ledger with micro-batch inserts.
3. **DuckDB / ClickHouse Engine for Analytics:** Evaluate DuckDB for sub-second analytical aggregations directly over Parquet files for 10M+ lead scale.
4. **Data Contract CI/CD Validation:** Add Great Expectations or Pandera checks as a pre-commit / CI gate for newly added workflow schemas.
