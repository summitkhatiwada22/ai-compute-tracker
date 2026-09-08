#!/usr/bin/env python3
"""
fetch_funding.py

AI-and-adjacent funding snapshot — Dealroom only, deliberately kept simple.

Dealroom (dealroom.co/for-agents/) is a free, public, no-key JSON API
that curates 170+ sector "market maps." This script queries several
tags relevant to this tracker's beat — not just "AI" — and logs one
row per tag per run.

TIME-FRAME NOTE, IMPORTANT: `totalFunding` per company (and therefore
`sample_funding_usd` here) appears to be a CUMULATIVE ALL-TIME total —
"everything this company has ever raised" — not a period-bound figure
like "Q1 2026 deal value" the way PitchBook's Venture Monitor works.
There's no start/end date field in Dealroom's documented response.
Running this DAILY is what makes the data usable as a period signal
despite that: the DIFFERENCE between today's total and yesterday's
approximates "new funding recorded in that window." That differencing
happens at analysis time, not in this script — each row here is just
a snapshot of the cumulative total as of that day.

TAGS TRACKED: ALL of them, discovered dynamically each run from
Dealroom's own 'availableTags' field — not a curated subset. Same
"discover, don't hardcode" pattern used for GPU model discovery in the
marketplace pricing pipeline: if Dealroom adds or removes a sector tag,
this picks it up automatically with no code change. Correlation across
categories (e.g. does "Energy" funding move with "Data Centers" capex)
can be explored at analysis time from whatever the full set turns out
to contain, rather than deciding in advance which categories matter.

WHY DEALROOM ALONE, NOT CROSS-REFERENCED AGAINST SEC:
An earlier version of this pipeline also tried to cross-check company
names against SEC's Form D bulk dataset. That added a second live,
unverified API, a fuzzy company-name-matching problem, and a whole
extra failure mode — real complexity for a series that Dealroom alone
already answers cleanly. Cut deliberately, not by oversight.

METHODOLOGY NOTE for any future methodology page: this reflects
Dealroom's own private research and sector categorization, not an
independently verifiable government figure. `total_companies` per tag
is Dealroom's own reported count and can be trusted directly.
`sample_funding_usd` is NOT a true category total — Dealroom's public
API only returns a capped sample of companies per list (`sample_size`
shows exactly how many), so it's a partial, directional lower bound.

No account, no API key, no cost — nothing to sign up for. Runs DAILY
(see the matching workflow) — cheap enough that there's no cost to
checking daily, and daily is what makes the differencing described
above possible.

    python scripts/fetch_funding.py

Writes to ONE persistent, append-only log, one row per tag per run:
    data/raw/funding/ai_market_snapshot.csv
"""

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# Fallback used only if live tag discovery fails entirely — the tags
# actually used each run come from discover_available_tags() below.
FALLBACK_TAGS = ["AI", "Data Centers", "Semiconductors", "Cloud", "Deep Tech"]

DEALROOM_MARKETMAPS_URL = "https://dealroom.co/api/marketmaps"
DEALROOM_MARKETMAP_URL = "https://dealroom.co/api/marketmap"
REQUEST_TIMEOUT_SECONDS = 30

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "funding"
SNAPSHOT_PATH = OUTPUT_DIR / "ai_market_snapshot.csv"

FIELDNAMES = [
    "collected_at", "source", "tag", "market_map_id", "market_map_title",
    "total_companies", "sample_funding_usd", "sample_size", "is_capped",
    "segment_count",
]


def discover_available_tags() -> list[str]:
    """Fetch the full list of sector tags Dealroom currently supports,
    rather than hardcoding a fixed list — the same 'discover, don't
    hardcode' pattern used for GPU model discovery elsewhere in this
    tracker. Falls back to a small known-good list only if this fails
    entirely (e.g. Dealroom's API is briefly unreachable)."""
    try:
        resp = requests.get(DEALROOM_MARKETMAPS_URL, params={"limit": 1}, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        print(f"[warn] tag discovery failed, using fallback list: {exc}", file=sys.stderr)
        return FALLBACK_TAGS

    if resp.status_code != 200:
        print(f"[warn] tag discovery HTTP {resp.status_code}, using fallback list", file=sys.stderr)
        return FALLBACK_TAGS

    tags = resp.json().get("availableTags", [])
    if not tags:
        print("[warn] discovery returned zero tags, using fallback list", file=sys.stderr)
        return FALLBACK_TAGS

    print(f"[diagnostic] discovered {len(tags)} available tags: {tags}")
    return tags


def fetch_market_snapshot_for_tag(tag: str) -> dict | None:
    """Query Dealroom's free API for the broadest ACCESSIBLE market map
    under one tag.

    FIX: confirmed live that every 404 correlates with a custom-slug map
    ID (e.g. "terrestrial-ai-compute", "market-intelligence-landscape")
    rather than the numeric "landscape-XXXXX" format — Dealroom's public
    marketmap detail endpoint appears to only serve the numeric-ID maps,
    even though custom-slug ones appear in search results. An earlier
    version always picked the single broadest candidate and gave up if
    it 404'd. This version tries candidates in descending order by
    company count and falls back to the next one on a 404, recovering
    any tag where a working alternative exists — only reports "no
    snapshot" if every candidate for that tag fails.
    """
    try:
        search_resp = requests.get(
            DEALROOM_MARKETMAPS_URL, params={"tag": tag, "limit": 20},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        print(f"[warn] Dealroom marketmaps search failed for tag={tag}: {exc}", file=sys.stderr)
        return None

    if search_resp.status_code != 200:
        print(f"[warn] Dealroom HTTP {search_resp.status_code} for tag={tag}: {search_resp.text[:300]}",
              file=sys.stderr)
        return None

    search_data = search_resp.json()
    results = search_data.get("results", [])
    print(f"[diagnostic] tag={tag}: {len(results)} candidate map(s) — "
          f"{[(r.get('title'), r.get('companyCount')) for r in results]}")

    if not results:
        print(f"[warn] No market maps found for tag={tag}", file=sys.stderr)
        return None

    # Try candidates broadest-first, falling back on a 404 instead of
    # giving up after the single top pick.
    ranked = sorted(results, key=lambda r: r.get("companyCount", 0) or 0, reverse=True)

    for candidate in ranked:
        map_id = candidate.get("id")
        try:
            detail_resp = requests.get(
                DEALROOM_MARKETMAP_URL, params={"id": map_id},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            print(f"[warn] Dealroom marketmap detail fetch failed for tag={tag}, id={map_id}: {exc}",
                  file=sys.stderr)
            continue

        if detail_resp.status_code == 404:
            print(f"[diagnostic]   {map_id} ('{candidate.get('title')}') 404'd — trying next candidate", file=sys.stderr)
            continue
        if detail_resp.status_code != 200:
            print(f"[warn] Dealroom detail HTTP {detail_resp.status_code} for tag={tag}, id={map_id}", file=sys.stderr)
            continue

        # Found a working candidate.
        print(f"  tag={tag}: using '{candidate.get('title')}' "
              f"({candidate.get('companyCount')} companies, id={map_id})")
        detail = detail_resp.json()
        print(f"[diagnostic]   returned={detail.get('returned')}, capped={detail.get('capped')}, "
              f"note={detail.get('note')}")

        companies = detail.get("companies", [])
        sample_funding = sum(
            (c.get("totalFunding") or {}).get("amount", 0) or 0
            for c in companies
        ) or None

        return {
            "tag": tag,
            "market_map_id": map_id,
            "market_map_title": candidate.get("title"),
            "total_companies": detail.get("total_companies"),
            "segment_count": len(detail.get("segments", [])),
            "sample_funding_usd": sample_funding,
            "sample_size": len(companies),
            "is_capped": detail.get("capped"),
        }

    print(f"[warn] tag={tag}: every candidate map failed (likely all custom-slug, "
          f"not accessible via the public detail endpoint) — no snapshot this run", file=sys.stderr)
    return None


def main():
    now = datetime.now(timezone.utc)
    rows = []

    tags = discover_available_tags()
    print(f"Tracking {len(tags)} tag(s) this run: {tags}")

    for tag in tags:
        print(f"\nFetching Dealroom snapshot for tag={tag}...")
        snapshot = fetch_market_snapshot_for_tag(tag)
        if snapshot:
            rows.append({"collected_at": now.isoformat(), "source": "dealroom", **snapshot})
        else:
            print(f"  No snapshot logged for tag={tag} this run — see warnings above.")

    if not rows:
        sys.exit("No snapshots could be built for any tag this run — see warnings above.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = SNAPSHOT_PATH.exists()
    with open(SNAPSHOT_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)

    print(f"\nLogged {len(rows)} tag snapshot(s) to {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
