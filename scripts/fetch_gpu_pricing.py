#!/usr/bin/env python3
"""
fetch_gpu_pricing.py

Pulls current GPU rental pricing from the Vast.ai marketplace (the
"marketplace" tier of the tracker's two-tier GPU pricing series) and
appends a dated snapshot to data/raw/vast_ai/.

GPU models tracked are NOT hardcoded. Each run first discovers every GPU
model currently listed on the marketplace, then pulls pricing for each
one it found. New GPU generations show up automatically over time with
no code changes.

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

DISCOVERY_SAMPLE_SIZE = 2000
OFFERS_PER_MODEL = 20

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "vast_ai"


def discover_gpu_models(vast_client: VastAI) -> list[str]:
    """Find every distinct GPU model currently listed on the marketplace."""
    try:
        offers = vast_client.search_offers(
            query="verified=true rentable=true",
            type="on-demand",
            order="dph_total",
            limit=str(DISCOVERY_SAMPLE_SIZE),
        )
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Discovery query failed — check API key/connectivity: {exc}")

    models = sorted({o.get("gpu_name") for o in (offers or []) if o.get("gpu_name")})
    if not models:
        sys.exit("Discovery query returned zero GPU models — something's wrong upstream.")
    return models


def fetch_offers(vast_client: VastAI, gpu_model: str, limit: int = OFFERS_PER_MODEL):
    """Fetch current on-demand offers for a given GPU model, cheapest first."""
    query = f"gpu_name={gpu_model} num_gpus=1 verified=true rentable=true"
    try:
        offers = vast_client.search_offers(
            query=query,
            type="on-demand",
            order="dph_total",
            limit=str(limit),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] search failed for {gpu_model}: {exc}", file=sys.stderr)
        return []
    return offers or []


def main():
    api_key = os.environ.get("VAST_API_KEY")
    if not api_key:
        sys.exit("VAST_API_KEY environment variable not set.")

    vast = VastAI(api_key=api_key)

    now = datetime.now(timezone.utc)
    run_date = now.strftime("%Y-%m-%d")
    run_ts = now.isoformat()

    print("Discovering currently listed GPU models...")
    gpu_models = discover_gpu_models(vast)
    print(f"Found {len(gpu_models)} GPU models: {', '.join(gpu_models)}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{run_date}.csv"

    rows = []
    for gpu_model in gpu_models:
        offers = fetch_offers(vast, gpu_model)
        print(f"  {gpu_model}: {len(offers)} offers")
        for offer in offers:
            rows.append(
                {
                    "collected_at": run_ts,
                    "provider": "vast.ai",
                    "tier": "marketplace",
                    "gpu_model": gpu_model,
                    "price_usd_per_hr": offer.get("dph_total"),
                    "num_gpus": offer.get("num_gpus"),
                    "region": offer.get("geolocation"),
                    "reliability": offer.get("reliability2"),
                    "offer_id": offer.get("id"),
                }
            )

    if not rows:
        sys.exit(
            "No offers returned for any discovered GPU model — "
            "check the API key and query syntax before assuming the market is just empty."
        )

    fieldnames = list(rows[0].keys())
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows across {len(gpu_models)} GPU models to {out_path}")


if __name__ == "__main__":
    main()
