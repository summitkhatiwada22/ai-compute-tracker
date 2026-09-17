#!/usr/bin/env python3
"""
build_public_summary.py

Reads data/tracker.duckdb and writes small, pre-aggregated JSON files to
data/public/ for the website's charts to fetch directly over HTTP. This
exists so the site's JavaScript never has to fetch or parse the full
daily CSV history — which grows every day, forever — it fetches these
few small, fixed-shape JSON files instead.

Run by hand:
    python scripts/build_public_summary.py

Runs automatically in CI right after build_database.py, in every fetch
workflow, so the public summary is never more than one pipeline run stale.
"""

import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "tracker.duckdb"
OUT_DIR = ROOT / "data" / "public"


def _rows(con, query):
    result = con.execute(query)
    cols = [d[0] for d in result.description]
    return [dict(zip(cols, r)) for r in result.fetchall()]


def gpu_pricing_overall(con):
    """One row per (date, tier): a single market-wide median $/hr across
    all GPU models — this is what actually renders as the headline
    marketplace-vs-neocloud comparison chart."""
    return _rows(con, """
        SELECT
            CAST(collected_at AS DATE) AS date,
            tier,
            COUNT(*) AS num_listings,
            ROUND(MEDIAN(price_usd_per_hr), 4) AS median_price_usd_per_hr
        FROM (
            SELECT collected_at, tier, price_usd_per_hr FROM gpu_pricing_marketplace
            UNION ALL
            SELECT collected_at, tier, price_usd_per_hr FROM gpu_pricing_neocloud_lambda
        )
        GROUP BY 1, 2
        ORDER BY 1, 2
    """)


def gpu_pricing_by_model(con):
    """One row per (date, tier, gpu_model) — for anyone who wants to
    drill into a specific model, not used by the headline chart."""
    return _rows(con, """
        SELECT
            CAST(collected_at AS DATE) AS date,
            tier,
            gpu_model,
            COUNT(*) AS num_listings,
            ROUND(MEDIAN(price_usd_per_hr), 4) AS median_price_usd_per_hr
        FROM (
            SELECT collected_at, tier, gpu_model, price_usd_per_hr FROM gpu_pricing_marketplace
            UNION ALL
            SELECT collected_at, tier, gpu_model, price_usd_per_hr FROM gpu_pricing_neocloud_lambda
        )
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
    """)


def hyperscaler_financials(con):
    """Already small (6 companies x 3 metrics x quarterly history) —
    passed through with just the columns the chart needs."""
    return _rows(con, """
        SELECT company, metric, period_end, fiscal_year, fiscal_period, form, value_usd
        FROM hyperscaler_financials
        ORDER BY company, metric, period_end
    """)


def funding_latest(con):
    """Latest snapshot only, ranked by total_companies — funding updates
    infrequently, so a single latest-per-map row is what the site needs,
    not full daily history of a series that barely moves day to day."""
    return _rows(con, """
        SELECT market_map_title, selected_for_tags, total_companies,
               sample_funding_usd, sample_size, is_capped,
               CAST(collected_at AS VARCHAR) AS collected_at
        FROM ai_market_snapshot
        WHERE collected_at = (SELECT MAX(collected_at) FROM ai_market_snapshot)
        ORDER BY total_companies DESC
    """)


def main():
    if not DB_PATH.exists():
        raise SystemExit(f"{DB_PATH} not found — run build_database.py first.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH), read_only=True)

    outputs = {
        "gpu_pricing_overall.json": gpu_pricing_overall(con),
        "gpu_pricing_by_model.json": gpu_pricing_by_model(con),
        "hyperscaler_financials.json": hyperscaler_financials(con),
        "funding_latest.json": funding_latest(con),
    }

    for filename, data in outputs.items():
        out_path = OUT_DIR / filename
        with open(out_path, "w") as f:
            json.dump(data, f, default=str)
        print(f"[ok] wrote {len(data)} row(s) to {out_path}")

    con.close()


if __name__ == "__main__":
    main()
