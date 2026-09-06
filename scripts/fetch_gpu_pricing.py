#!/usr/bin/env python3
"""
fetch_gpu_pricing.py

Pulls current GPU rental pricing from the Vast.ai marketplace (the
"marketplace" tier of the tracker's two-tier GPU pricing series) and
appends a dated snapshot to data/raw/vast_ai/.

FIX #1: `limit` is passed as an int, not a string — Vast.ai's SDK types
it as Optional[int], and passing a string silently returned far fewer
results than requested.

FIX #2 (superseded by this version's redesign, kept for history): an
earlier per-model design built `gpu_name={model}` unquoted, which broke
on any model name containing a space ("RTX 4090", "Tesla T4"). This
version no longer filters by gpu_name at all (see below), so this class
of bug can't recur.

FIX #3 / REDESIGN (this version): a single price-sorted query caps out
at ~512 results no matter what limit is requested — confirmed live
(requested limit=2000, got exactly 512 back). Sorted cheapest-first,
that silently excluded every expensive listing (H100, H100 SXM, H200,
B200). A two-query cheap+expensive sample (previous version) covered
both extremes but could still miss whatever falls in the middle if
total inventory is large.

This version fixes it properly: instead of one query or two, it slices
the market into narrow PRICE BUCKETS (see PRICE_BUCKET_EDGES) and
queries each one separately with `dph_total>=lo dph_total<hi`. As long
as no single bucket's listings exceed the ~512-1000 cap, this covers
the ENTIRE current market with no gap — cheap consumer cards and H100s
and everything between, in one pass. Each returned offer carries its
own gpu_name, so there's no separate "discover models" step anymore.
The script prints a warning if any bucket comes close to the cap, which
is the signal to split that bucket further.

Also now captures gpu_ram (VRAM), dlperf (a deep-learning performance
score), and dlperf_per_dphtotal (performance-per-dollar) from each
offer — useful for a cost-per-FLOP-controlled-for-generation analysis,
not just raw $/hr. Power draw/wattage is NOT available from this API —
it's a static hardware spec, not a rental-listing field.

    export VAST_API_KEY="your-key-here"
    python scripts/fetch_gpu_pricing.py

Each run writes ONE file: data/raw/vast_ai/YYYY-MM-DD.csv
Running it twice on the same UTC day overwrites that day's file rather
than duplicating rows — safe to re-run. Every PAST day's file is left
untouched.
"""

import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from vastai import VastAI

# Price band edges in $/hr. Each consecutive pair defines one query:
# dph_total >= edges[i] AND dph_total < edges[i+1]. The final band is
# open-ended (dph_total >= last edge), to catch anything above $40/hr
# (multi-GPU-node-equivalent per-GPU pricing outliers, etc.).
# Finer resolution near the bottom, where most consumer-card listings
# cluster and a single band is more likely to hit the results cap.
PRICE_BUCKET_EDGES = [
    0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75,
    1, 1.5, 2, 3, 5, 8, 12, 20, 40,
]

# Max results requested per price band. Each band should comfortably
# stay under this — if a [diagnostic] line below reports a count close
# to this number, that band is too wide and needs splitting further.
BUCKET_QUERY_LIMIT = 2000
CAP_WARNING_THRESHOLD = int(BUCKET_QUERY_LIMIT * 0.9)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "vast_ai"


def _as_list(offers):
    """search_offers() should return a list of dicts. Guard against the
    edge case where it comes back as something else (empty, None, or a
    raw string) so a bad response fails loudly instead of silently."""
    if offers is None:
        return []
    if isinstance(offers, list):
        return offers
    print(f"[warn] unexpected response type from search_offers: {type(offers)}", file=sys.stderr)
    return []


def fetch_all_offers(vast_client: VastAI) -> list[dict]:
    """Fetch every current on-demand, single-GPU, verified/rentable
    offer across the whole market, by querying narrow price bands one
    at a time instead of one query that hits the API's results cap."""
    all_offers = []
    edges = PRICE_BUCKET_EDGES + [None]  # None = no upper bound on the last band

    for i in range(len(edges) - 1):
        lo = edges[i]
        hi = edges[i + 1]
        if hi is not None:
            query = f"verified=true rentable=true num_gpus=1 dph_total>={lo} dph_total<{hi}"
            label = f"${lo}-{hi}/hr"
        else:
            query = f"verified=true rentable=true num_gpus=1 dph_total>={lo}"
            label = f"${lo}+/hr"

        try:
            raw_offers = vast_client.search_offers(
                query=query,
                type="on-demand",
                order="dph_total",
                limit=BUCKET_QUERY_LIMIT,
            )
        except Exception as exc:  # noqa: BLE001 - log and keep going with other bands
            print(f"[warn] price band {label} failed: {exc}", file=sys.stderr)
            continue

        offers = _as_list(raw_offers)
        cap_flag = " <-- AT/NEAR CAP: split this band further" if len(offers) >= CAP_WARNING_THRESHOLD else ""
        print(f"[diagnostic] band {label}: {len(offers)} offers{cap_flag}")
        all_offers.extend(offers)

    return all_offers


def main():
    api_key = os.environ.get("VAST_API_KEY")
    if not api_key:
        sys.exit("VAST_API_KEY environment variable not set.")

    vast = VastAI(api_key=api_key)

    now = datetime.now(timezone.utc)
    run_date = now.strftime("%Y-%m-%d")
    run_ts = now.isoformat()

    print("Fetching offers across all price bands...")
    offers = fetch_all_offers(vast)

    if not offers:
        sys.exit("No offers returned across any price band — check API key/connectivity.")

    # One-time visibility into every field Vast.ai actually returns, so
    # future field additions don't require another guess-and-check round.
    print(f"[diagnostic] example raw offer keys: {sorted(offers[0].keys())}")

    models = sorted({o.get("gpu_name") for o in offers if o.get("gpu_name")})
    print(f"Collected {len(offers)} offers across {len(models)} distinct GPU models: {', '.join(models)}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{run_date}.csv"

    rows = [
        {
            "collected_at": run_ts,
            "provider": "vast.ai",
            "tier": "marketplace",
            "gpu_model": offer.get("gpu_name"),
            "price_usd_per_hr": offer.get("dph_total"),
            "num_gpus": offer.get("num_gpus"),
            "gpu_ram_gb": offer.get("gpu_ram"),
            "dlperf": offer.get("dlperf"),
            "dlperf_per_dollar": offer.get("dlperf_per_dphtotal"),
            "cpu_ram_gb": offer.get("cpu_ram"),
            "disk_space_gb": offer.get("disk_space"),
            "region": offer.get("geolocation"),
            "reliability": offer.get("reliability2"),
            "offer_id": offer.get("id"),
        }
        for offer in offers
    ]

    fieldnames = list(rows[0].keys())
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows across {len(models)} GPU models to {out_path}")


if __name__ == "__main__":
    main()
