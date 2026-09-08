#!/usr/bin/env python3
"""
fetch_funding.py

AI funding-volume series — Dealroom only, deliberately kept simple.

Dealroom (dealroom.co/for-agents/) is a free, public, no-key JSON API
that curates 170+ sector "market maps," including AI as its own
defined market. This script queries that map and logs an aggregate
snapshot: total AI companies tracked, total funding, segment count.

WHY DEALROOM ALONE, NOT CROSS-REFERENCED AGAINST SEC:
An earlier version of this pipeline also tried to cross-check company
names against SEC's Form D bulk dataset. That added a second live,
unverified API, a fuzzy company-name-matching problem, and a whole
extra failure mode — real complexity for a series that Dealroom alone
already answers cleanly. Cut deliberately, not by oversight. If
single-source risk (Dealroom changes or restricts its free tier, the
way Crunchbase did) ever becomes a real problem, the SEC Form D
cross-check is a documented, addable fast-follow — same pattern as the
CoreWeave scraper being a "fast-follow, not a launch blocker" in the
original tracker plan.

METHODOLOGY NOTE for any future methodology page: this reflects
Dealroom's own private research and sector categorization, not an
independently verifiable government figure — unlike the capex/SEC
series. State that plainly if this number gets cited anywhere.

HONESTY NOTE: Dealroom's exact live JSON response shape could not be
verified from a sandboxed environment with no network access to it —
only their documented shape. Expect this to need a small fix after the
first real run, the same way earlier pipelines did.

No account, no API key, no cost — nothing to sign up for. Runs DAILY
(see the matching workflow) — cheap enough that there's no cost to
checking daily, even though the underlying number often won't have
changed since yesterday.

    python scripts/fetch_funding.py

Writes to ONE persistent, append-only log:
    data/raw/funding/ai_market_snapshot.csv
"""

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

DEALROOM_MARKETMAPS_URL = "https://dealroom.co/api/marketmaps"
DEALROOM_MARKETMAP_URL = "https://dealroom.co/api/marketmap"
REQUEST_TIMEOUT_SECONDS = 30

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "funding"
SNAPSHOT_PATH = OUTPUT_DIR / "ai_market_snapshot.csv"

FIELDNAMES = [
    "collected_at", "source", "market_map_id", "market_map_title",
    "total_companies", "total_funding_usd", "segment_count",
]


def fetch_ai_market_snapshot() -> dict | None:
    """Query Dealroom's free API for the AI sector market map."""
    try:
        search_resp = requests.get(
            DEALROOM_MARKETMAPS_URL, params={"q": "AI", "limit": 5},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        print(f"[warn] Dealroom marketmaps search failed: {exc}", file=sys.stderr)
        return None

    if search_resp.status_code != 200:
        print(f"[warn] Dealroom HTTP {search_resp.status_code}: {search_resp.text[:300]}", file=sys.stderr)
        return None

    search_data = search_resp.json()
    print(f"[diagnostic] Dealroom marketmaps search returned: {search_data}")

    results = search_data.get("results", [])
    if not results:
        print("[warn] No AI market map found in Dealroom search results", file=sys.stderr)
        return None

    top_map = results[0]
    map_id = top_map.get("id")
    print(f"Using market map: {top_map.get('title')} (id={map_id})")

    try:
        detail_resp = requests.get(
            DEALROOM_MARKETMAP_URL, params={"id": map_id},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        print(f"[warn] Dealroom marketmap detail fetch failed: {exc}", file=sys.stderr)
        return None

    if detail_resp.status_code != 200:
        print(f"[warn] Dealroom detail HTTP {detail_resp.status_code}", file=sys.stderr)
        return None

    detail = detail_resp.json()
    print(f"[diagnostic] Dealroom marketmap detail keys: {list(detail.keys())}")

    companies = detail.get("companies", [])
    total_funding = sum(
        (c.get("totalFunding") or {}).get("amount", 0) or 0
        for c in companies
    ) or None

    return {
        "market_map_id": map_id,
        "market_map_title": top_map.get("title"),
        "total_companies": detail.get("total_companies"),
        "segment_count": len(detail.get("segments", [])),
        "total_funding_usd": total_funding,
    }


def main():
    print("Fetching Dealroom AI market snapshot...")
    snapshot = fetch_ai_market_snapshot()

    if not snapshot:
        sys.exit("No snapshot could be built this run — see warnings above.")

    now = datetime.now(timezone.utc)
    row = {"collected_at": now.isoformat(), "source": "dealroom", **snapshot}

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = SNAPSHOT_PATH.exists()
    with open(SNAPSHOT_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    print(f"Logged snapshot to {SNAPSHOT_PATH}: {row}")


if __name__ == "__main__":
    main()
