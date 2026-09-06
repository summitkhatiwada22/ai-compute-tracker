#!/usr/bin/env python3
"""
fetch_gpu_pricing.py

Pulls current GPU rental pricing from the Vast.ai marketplace (the
"marketplace" tier of the tracker's two-tier GPU pricing series) and
appends a dated snapshot to data/raw/vast_ai/.

FIX (vs. earlier version): `limit` is now passed as an int, not a
string. Vast.ai's SDK types `limit` as Optional[int]; passing a string
silently produced far fewer results than requested (observed: 8 rows
across 3 GPU models, when the real marketplace lists thousands across
dozens of models). An earlier attempt at this fix also added a
`disable_bundling` argument based on third-party docs — that parameter
does not actually exist on the installed vastai package (confirmed via
inspect.signature on the real installed version) and was removed.

GPU models tracked are NOT hardcoded — each run discovers every GPU
model currently listed, then pulls pricing for each one found.

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

# How many current listings to sample when discovering which GPU models
# exist right now. High enough to see the long tail, not so high it's slow.
DISCOVERY_SAMPLE_SIZE = 2000

# How many offers to keep per discovered GPU model, cheapest first.
OFFERS_PER_MODEL = 20


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


OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "vast_ai"


def discover_gpu_models(vast_client: VastAI) -> list[str]:
    """Find every distinct GPU model currently listed on the marketplace."""
    try:
        raw_offers = vast_client.search_offers(
            query="verified=true rentable=true",
            type="on-demand",
            order="dph_total",
            limit=DISCOVERY_SAMPLE_SIZE,  # int, not str — see module docstring
        )
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Discovery query failed — check API key/connectivity: {exc}")

    offers = _as_list(raw_offers)
    print(f"[diagnostic] discovery call returned {len(offers)} raw offers "
          f"(requested up to {DISCOVERY_SAMPLE_SIZE})")

    models = sorted({o.get("gpu_name") for o in offers if o.get("gpu_name")})
    if not models:
        sys.exit("Discovery query returned zero GPU models — something's wrong upstream.")
    if len(offers) < 100:
        print(
            "[warn] discovery returned suspiciously few offers for a live marketplace — "
            "double check the query/limit before trusting this run's data.",
            file=sys.stderr,
        )
    return models


def fetch_offers(vast_client: VastAI, gpu_model: str, limit: int = OFFERS_PER_MODEL):
    """Fetch current on-demand offers for a given GPU model, cheapest first."""
    query = f"gpu_name={gpu_model} num_gpus=1 verified=true rentable=true"
    try:
        raw_offers = vast_client.search_offers(
            query=query,
            type="on-demand",
            order="dph_total",
            limit=limit,  # int, not str
        )
    except Exception as exc:  # noqa: BLE001 - log and continue with other models
        print(f"[warn] search failed for {gpu_model}: {exc}", file=sys.stderr)
        return []
    return _as_list(raw_offers)


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
