# AI Usage Disclosure

## Tools used
- Antigravity / Gemini 3.8 Flash

## What I used them for
- Rapidly exploratory analysis of synthetic data defects and edge cases.
- Generating boilerplate for table schemas and SQLite connection management.
- Drafting initial parameter sets for regex expressions (currency prefixes, date formats).
- Writing initial test case permutations for phone numbers and dates.

## What I wrote or decided myself
- **Core Architecture & PII Security Strategy:** Designed the zero-knowledge deterministic joining mechanism (`phone_hash = SHA256(canonical_phone)`) combined with Fernet AES-128-CBC encryption for PII at rest and masked display format (`+91******XXXX`).
- **Idempotency & Drop Reconciliation Model:** Architected the append-only ledger and file-tracking mechanism so that events arriving in earlier drops before their lead are retained and reconciled dynamically in later drops.
- **In-Memory Hash Map with Batched DB Commits:** Conceived and implemented in-memory lookup caching during lead ingestion, speeding up `--scale large` (144k leads, 632k events) from >15 minutes down to 43.7 seconds.
- **Precedence Logic:** Strictly mapped out the exact multi-tier precedence for lead buckets (`yet_to_reach_out` > `completed` > `in_progress_telephony_failed` > `in_progress_dialed`) and schema validation order.

## Where the AI was wrong or unhelpful
- **Trailing Underscore Normalization:** Initial regex `re.sub(r"[\s_]+", " ", name.strip().lower())` left trailing spaces when input had trailing underscores (e.g. `Policy__Number_` became `'policy number '`), which caused header normalization assertions to fail. I corrected it to `re.sub(r"[\s_]+", " ", name.lower()).strip()`.
- **Field Warning Isolation on Rejected Rows:** Early draft implementations accumulated field warnings for all rows, including those that were subsequently rejected due to missing required fields or invalid phones. I intervened to ensure warnings are discarded for rejected rows, strictly adhering to the specification rule: *"Count warnings only for rows that are not rejected"*.
- **Orphan vs. Unknown Disposition Ordering:** Initially suggested counting unknown dispositions across all events. I corrected this to follow the spec's ordered pipeline (Step 3 Orphan before Step 4 Unknown Disposition), ensuring orphan events with uncatalogued dispositions are counted under orphans first.

## How I verified AI-generated code
- **Automated Test Suite:** Created unit and integration tests covering phone normalisation, date parsing, effective dispositions, bucket priorities, and end-to-end pipeline execution with `pytest`.
- **Multi-Seed Testing:** Ran the pipeline against both seed 42 and seed 123 to verify deterministic accounting across unseen datasets.
- **Large Dataset Stress Testing:** Benchmarked the pipeline against `--scale large` (144k leads, 632k events) to ensure memory efficiency and runtime performance (< 44 seconds).
- **Exact Checksum Verification:** Compared output hashes of incremental (`drop_1` then `drop_2`) versus all-at-once ingestion, confirming byte-for-byte identical reports.
