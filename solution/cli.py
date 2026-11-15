"""
Command-line interface for the data ingestion and reporting pipeline.
"""
from __future__ import annotations

import argparse
import sys
from solution.db import Database
from solution.ingest import ingest_drop
from solution.report import generate_reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Agentic Universe - Interaction Ledger & Reporting Pipeline",
    )
    parser.add_argument("--db", default="pipeline.db", help="Path to SQLite database file (default: pipeline.db)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Ingest command
    ingest_p = subparsers.add_parser("ingest", help="Ingest a data drop directory (leads and events)")
    ingest_p.add_argument("drop_dir", help="Path to drop directory (e.g. data/drop_1)")

    # Report command
    report_p = subparsers.add_parser("report", help="Generate final reports from database")
    report_p.add_argument("--out", default="reports", help="Output directory for reports (default: reports)")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    db = Database(args.db)

    if args.command == "ingest":
        print(f"Ingesting drop from {args.drop_dir} into {args.db}...")
        stats = ingest_drop(args.drop_dir, db)
        print("Ingestion complete. Metrics delta:")
        for k, v in stats.items():
            print(f"  {k}: {v}")

    elif args.command == "report":
        print(f"Generating reports into {args.out} using database {args.db}...")
        generate_reports(args.out, db)
        print("Done.")


if __name__ == "__main__":
    main()
