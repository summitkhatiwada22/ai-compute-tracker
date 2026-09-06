#!/usr/bin/env python3
"""
build_database.py

Rebuilds data/tracker.duckdb from every raw CSV snapshot under data/raw/.
The fetch scripts only ever write raw snapshots (never deleted); this
script is what turns the full history of those files into a real,
queryable database.

Safe to run repeatedly: it rebuilds each table from scratch FROM ALL THE
RAW CSVS COLLECTED SO FAR every time — old days are never dropped, only
re-read and re-included alongside the newest day.

    python scripts/build_database.py
"""

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "tracker.duckdb"

# table name -> glob of raw CSVs that feed it.
# Add an entry here each time a new raw data source is added
# (e.g. neocloud pricing, capex filings, funding data).
SOURCES = {
    "gpu_pricing_marketplace": RAW_DIR / "vast_ai" / "*.csv",
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
