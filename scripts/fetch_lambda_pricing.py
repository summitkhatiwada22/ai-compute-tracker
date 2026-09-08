#!/usr/bin/env python3
"""
fetch_lambda_pricing.py

Pulls current on-demand GPU instance pricing from Lambda Cloud's public
REST API — the first source in the "neocloud" tier of the tracker's
two-tier GPU pricing series (see fetch_gpu_pricing.py for the
marketplace tier, Vast.ai). Appends a dated snapshot to
data/raw/lambda_labs/.

API reference (confirmed from Lambda's own official docs at
docs-api.lambda.ai, cross-checked against their Python client and a
real community-published example response):
  GET https://cloud.lambda.ai/api/v1/instance-types
  Header: Authorization: Bearer <api_key>

Response shape (confirmed, not guessed):
  {
    "data": {
      "gpu_1x_a10": {
        "instance_type": {
          "name": "gpu_1x_a10",
          "price_cents_per_hour": 60,
          "description": "1x A10 (24 GB PCIe)",
          "specs": {"vcpus": 30, "memory_gib": 200, "storage_gib": 1400}
        },
        "regions_with_capacity_available": [
          {"name": "us-west-1", "description": "California, USA"}
        ]
      },
      ...
    }
  }
Some published examples show this same mapping WITHOUT the top-level
"data" wrapper, so fetch_instance_types() below handles both shapes
rather than assuming one.

UNIT NOTE: price_cents_per_hour is CENTS, not dollars — divided by 100
below. Checking this explicitly rather than assuming, after the
Vast.ai gpu_ram MB-vs-GB mislabeling earlier.

SCOPE NOTE: Lambda's endpoint returns every instance type they sell,
including CPU-only SKUs (e.g. "cpu_4x_general") — out of scope for a
GPU pricing tracker. These are filtered out explicitly below rather
than falling through with blank gpu_model/num_gpus fields, which is
what an earlier version of this script did.

Runs DAILY, same cadence as the Vast.ai marketplace fetch — even though
neocloud rate cards change far less often, a daily pull is what lets
the tracker pin down the exact day a price actually moved, not just
"sometime this week." The API call is cheap and static, so there's no
real cost to running it daily instead of weekly.

    export LAMBDA_API_KEY="your-key-here"
    python scripts/fetch_lambda_pricing.py

Each run writes ONE file: data/raw/lambda_labs/YYYY-MM-DD.csv
Safe to re-run same-day; past days are never touched.
"""

import csv
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://cloud.lambda.ai/api/v1/instance-types"
REQUEST_TIMEOUT_SECONDS = 30

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "lambda_labs"

# Matches "gpu_8x_a100_80gb_sxm4" -> num_gpus=8, model_slug="a100_80gb_sxm4"
NAME_PATTERN = re.compile(r"^gpu_(\d+)x_(.+)$")


def fetch_instance_types(api_key: str) -> dict:
    """Call Lambda's instance-types endpoint and return the raw
    instance_type_name -> details mapping. Handles the response with or
    without a top-level "data" wrapper, since published examples
    disagree on this."""
    try:
        response = requests.get(
            API_URL,
            headers={"Authorization": f"Bearer {api_key}", "accept": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        sys.exit(f"Request to Lambda API failed: {exc}")

    if response.status_code != 200:
        sys.exit(f"Lambda API returned HTTP {response.status_code}: {response.text[:500]}")

    payload = response.json()
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    if isinstance(payload, dict):
        return payload
    sys.exit(f"Unexpected response shape from Lambda API: {type(payload)}")


def build_row(name: str, entry: dict, run_ts: str) -> dict:
    """Turn one raw instance-type entry into a flat CSV row."""
    instance_type = entry.get("instance_type", {})
    specs = instance_type.get("specs", {})
    regions = entry.get("regions_with_capacity_available", []) or []

    match = NAME_PATTERN.match(name)
    num_gpus = int(match.group(1)) if match else None
    model_slug = match.group(2) if match else name

    price_cents = instance_type.get("price_cents_per_hour")
    price_usd_per_hr = round(price_cents / 100, 4) if price_cents is not None else None

    return {
        "collected_at": run_ts,
        "provider": "lambda_labs",
        "tier": "neocloud",
        "instance_type_name": name,
        "gpu_model": model_slug,
        "num_gpus": num_gpus,
        "price_usd_per_hr": price_usd_per_hr,
        "description": instance_type.get("description"),
        "vcpus": specs.get("vcpus"),
        "memory_gib": specs.get("memory_gib"),
        "storage_gib": specs.get("storage_gib"),
        "regions_with_capacity": ";".join(r.get("name", "") for r in regions),
        "num_regions_with_capacity": len(regions),
    }


def main():
    api_key = os.environ.get("LAMBDA_API_KEY")
    if not api_key:
        sys.exit("LAMBDA_API_KEY environment variable not set.")

    now = datetime.now(timezone.utc)
    run_date = now.strftime("%Y-%m-%d")
    run_ts = now.isoformat()

    print("Fetching Lambda Cloud instance types...")
    instance_map = fetch_instance_types(api_key)
    print(f"[diagnostic] received {len(instance_map)} instance types (all types, including non-GPU)")

    if not instance_map:
        sys.exit("Zero instance types returned — check API key/connectivity before trusting this run.")

    # Lambda's endpoint returns everything they sell, including CPU-only
    # SKUs (e.g. "cpu_4x_general") — out of scope for a GPU pricing
    # tracker, and previously fell through with blank gpu_model/num_gpus
    # fields rather than being excluded outright. Filter to GPU instance
    # types only, based on Lambda's own naming convention.
    gpu_instance_map = {name: entry for name, entry in instance_map.items() if name.startswith("gpu_")}
    skipped = sorted(set(instance_map) - set(gpu_instance_map))
    if skipped:
        print(f"[diagnostic] excluding {len(skipped)} non-GPU instance type(s): {', '.join(skipped)}")

    if not gpu_instance_map:
        sys.exit("Zero GPU instance types after filtering — check API key/connectivity before trusting this run.")

    example_key = next(iter(gpu_instance_map))
    print(f"[diagnostic] example raw entry ({example_key}): {gpu_instance_map[example_key]}")

    rows = [build_row(name, entry, run_ts) for name, entry in gpu_instance_map.items()]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{run_date}.csv"

    fieldnames = list(rows[0].keys())
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
