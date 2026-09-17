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
    """One row per (date, tier): median $/hr per GPU, restricted to GPU
    models that are genuinely the same hardware across both tiers.

    Two separate problems had to be fixed to get here:

    1. Naming mismatch. Vast.ai (marketplace) and Lambda Cloud (neocloud)
       name the same chips completely differently — e.g. Vast writes
       "H100 SXM", Lambda writes "h100_sxm5". An exact-string join between
       the two never matches, so this maps Lambda's slugs onto Vast's
       display names for pairs confirmed to be the same hardware (checked
       against Lambda's own published instance descriptions):
         - "rtx6000" -> Lambda documents this as 24 GB VRAM, i.e. the
           older Quadro RTX 6000, not the 48 GB RTX 6000 Ada. Vast.ai
           lists that exact card separately as "Q RTX 6000".
         - "a100" (bare) -> Lambda's A100 PCIe is documented as 40 GB;
           the SXM4 variants already have their own explicit slugs
           ("a100_sxm4", "a100_80gb_sxm4"), so by elimination the bare
           slug is the PCIe 40 GB offering -> "A100 PCIE".
       Only "gh200" (Grace Hopper Superchip) stays unmapped — Vast.ai
       doesn't sell that hardware at all, so there's no counterpart.

    2. Per-GPU vs. per-node price. Lambda's API field is named
       price_cents_per_hour but is actually the TOTAL price for the whole
       instance, not per GPU — confirmed directly from raw fetch data:
       gpu_8x_v100 costs $6.32/hr total for 8 GPUs ($0.79/GPU, matching
       Lambda's own published per-GPU rate exactly), not $6.32/GPU.
       gpu_1x_a6000 / gpu_2x_a6000 / gpu_4x_a6000 are $1.09 / $2.18 / $4.36
       — clean linear scaling by GPU count, confirming the field is a
       node total. Vast.ai's marketplace listings are always single-GPU
       (num_gpus = 1), so comparing Lambda's raw field directly against
       them compared "whole node" prices against "one GPU" prices. Fixed
       by dividing Lambda's price by its own num_gpus column before
       anything else touches it. This also explains why "v100" and
       "v100_n" looked like a duplicate bug earlier (both $6.32) — they're
       two distinct real Lambda SKUs (plain vs. NVLink-interconnected V100
       pods) that happen to cost the same per GPU; dividing reveals that
       rather than hiding it.
    """
    return _rows(con, """
        WITH marketplace AS (
            SELECT collected_at, tier, gpu_model, price_usd_per_hr
            FROM gpu_pricing_marketplace
        ),
        neocloud_mapped AS (
            SELECT
                collected_at,
                tier,
                CASE gpu_model
                    WHEN 'a10' THEN 'A10'
                    WHEN 'a6000' THEN 'RTX A6000'
                    WHEN 'h100_pcie' THEN 'H100 PCIE'
                    WHEN 'h100_sxm5' THEN 'H100 SXM'
                    WHEN 'b200_sxm6' THEN 'B200'
                    WHEN 'v100' THEN 'Tesla V100'
                    WHEN 'v100_n' THEN 'Tesla V100'
                    WHEN 'a100_sxm4' THEN 'A100 SXM4'
                    WHEN 'a100_80gb_sxm4' THEN 'A100 SXM4'
                    WHEN 'a100' THEN 'A100 PCIE'
                    WHEN 'rtx6000' THEN 'Q RTX 6000'
                    ELSE NULL
                END AS gpu_model,
                price_usd_per_hr / num_gpus AS price_usd_per_hr
            FROM gpu_pricing_neocloud_lambda
            WHERE num_gpus > 0
        ),
        per_tier AS (
            SELECT collected_at, tier, gpu_model, price_usd_per_hr FROM marketplace
            UNION ALL
            SELECT collected_at, tier, gpu_model, price_usd_per_hr FROM neocloud_mapped
            WHERE gpu_model IS NOT NULL
        ),
        common_models AS (
            SELECT CAST(collected_at AS DATE) AS date, gpu_model
            FROM per_tier
            GROUP BY 1, 2
            HAVING COUNT(DISTINCT tier) = 2
        )
        SELECT
            CAST(t.collected_at AS DATE) AS date,
            t.tier,
            COUNT(*) AS num_listings,
            ROUND(MEDIAN(t.price_usd_per_hr), 4) AS median_price_usd_per_hr
        FROM per_tier t
        JOIN common_models c
          ON CAST(t.collected_at AS DATE) = c.date AND t.gpu_model = c.gpu_model
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
