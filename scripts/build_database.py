#!/usr/bin/env python3
"""
build_database.py

Rebuilds data/tracker.duckdb from every raw CSV snapshot under data/raw/.
This is what actually turns the daily CSV files into a real, queryable
database — the fetch scripts only ever write raw snapshots; this script
is what "the database" means in this pipeline.

Safe to run repeatedly: it rebuilds each table from scratch from the raw
CSVs every time, rather than incrementally appending, so there's no risk
of double-counting a row or drifting out of sync with the source files.

Run by hand:
    python scripts/build_database.py

Runs automatically in CI right after each fetch script, via the GitHub
Actions workflows.
"""

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "tracker.duckdb"

# Maps: table name -> glob of raw CSVs that feed it.
# Add an entry here each time a new raw data source is added
# (e.g. neocloud pricing, capex filings, funding data).
SOURCES = {
    "gpu_pricing_marketplace": RAW_DIR / "vast_ai" / "*.csv",
    "gpu_pricing_neocloud_lambda": RAW_DIR / "lambda_labs" / "*.csv",
    # Single persistent log, not a glob of dated files — rows are
    # already timestamped by their own reporting period, so there's
    # only ever one file here, appended to over time by fetch_capex.py.
    # Table named for its actual scope (capex + revenue + operating
    # cash flow), even though the underlying script is still called
    # fetch_capex.py.
    "hyperscaler_financials": RAW_DIR / "capex" / "capex_financials_log.csv",
}


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))

    for table_name, csv_glob in SOURCES.items():
        matching_files = list(csv_glob.parent.glob(csv_glob.name))
        if not matching_files:
            print(f"[skip] no files yet for {table_name} ({csv_glob})")
            continue

        con.execute(f"DROP TABLE IF EXISTS {table_name}")
        con.execute(
            f"""
            CREATE TABLE {table_name} AS
            SELECT * FROM read_csv_auto('{csv_glob}', union_by_name=true)
            """
        )
        count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"[ok] {table_name}: {count} rows from {len(matching_files)} file(s)")

    con.close()
    print(f"Database rebuilt at {DB_PATH}")


if __name__ == "__main__":
    main()
def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))

    for table_name, csv_glob in SOURCES.items():
        matching_files = list(csv_glob.parent.glob(csv_glob.name))
        if not matching_files:
            print(f"[skip] no files yet for {table_name} ({csv_glob})")
            continue

        con.execute(f"DROP TABLE IF EXISTS {table_name}")
        con.execute(
            f"""
            CREATE TABLE {table_name} AS
            SELECT * FROM read_csv_auto('{csv_glob}', union_by_name=true)
            """
        )
        count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"[ok] {table_name}: {count} rows from {len(matching_files)} file(s)")

    con.close()
    print(f"Database rebuilt at {DB_PATH}")


if __name__ == "__main__":
    main()
