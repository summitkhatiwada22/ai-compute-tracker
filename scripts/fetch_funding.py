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

ACCESS NOTE: two live runs showed every 404 came from custom-slug map
IDs ("terrestrial-ai-compute", "market-intelligence-landscape",
"us-novel-ai-startups"...) — the shape of partner-built maps — while
every numeric "landscape-XXXXX" map worked. This version therefore
(1) follows each map's own documented `companiesUrl` before falling
back to a constructed URL, and (2) falls back to the documented
/api/third-party-maps catalogue for partner-built maps, logging their
company_count even when a funding sample isn't exposed. Rows carry an
`access_path` column (marketmap / companiesUrl / third_party_maps) so
you can always see which route produced a number — and rows reached
via third_party_maps have NO funding sample, only a company count.

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

Writes to ONE persistent log, ONE ROW PER MAP per day (a map chosen by
several tag queries appears once, with all of them in selected_for_tags;
Dealroom's own tag list for the map is in dealroom_tags). Re-running on
the same UTC day replaces that day's rows instead of duplicating them:
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
DEALROOM_THIRD_PARTY_MAPS_URL = "https://dealroom.co/api/third-party-maps"
REQUEST_TIMEOUT_SECONDS = 30

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "funding"
SNAPSHOT_PATH = OUTPUT_DIR / "ai_market_snapshot.csv"

FIELDNAMES = [
    "collected_at", "source", "market_map_id", "market_map_title",
    "selected_for_tags", "dealroom_tags",
    "total_companies", "sample_funding_usd", "sample_size", "is_capped",
    "segment_count", "access_path", "source_label",
]

# Cached once per run — the documented /api/third-party-maps catalogue of
# partner-built maps, keyed by slug and by lowercase title.
_THIRD_PARTY_MAPS: dict | None = None


def load_third_party_maps() -> dict:
    """Fetch Dealroom's documented /api/third-party-maps catalogue once.

    Returns {slug_or_lowercase_title: record}. Records carry
    company_count, public_url, source_label — enough to log
    total_companies for partner-built maps that the standard
    /api/marketmap detail endpoint refuses to serve (the custom-slug
    maps that 404'd in live runs: "Terrestrial compute: the AI
    build-out", "Market Intelligence Landscape", "US Novel AI
    Startups", etc.)."""
    global _THIRD_PARTY_MAPS
    if _THIRD_PARTY_MAPS is not None:
        return _THIRD_PARTY_MAPS

    _THIRD_PARTY_MAPS = {}
    try:
        resp = requests.get(DEALROOM_THIRD_PARTY_MAPS_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        print(f"[warn] third-party-maps fetch failed: {exc}", file=sys.stderr)
        return _THIRD_PARTY_MAPS

    if resp.status_code != 200:
        print(f"[warn] third-party-maps HTTP {resp.status_code}", file=sys.stderr)
        return _THIRD_PARTY_MAPS

    data = resp.json()
    records = data.get("results", []) if isinstance(data, dict) else data
    for rec in records or []:
        slug = rec.get("slug")
        title = (rec.get("title") or "").strip().lower()
        if slug:
            _THIRD_PARTY_MAPS[slug] = rec
        if title:
            _THIRD_PARTY_MAPS[title] = rec
    print(f"[diagnostic] third-party-maps catalogue: {len(records or [])} partner-built map(s) — "
          f"{[(r.get('slug'), r.get('title'), r.get('company_count')) for r in (records or [])][:40]}")
    return _THIRD_PARTY_MAPS


def _parse_detail(payload) -> dict | None:
    """Tolerate the two plausible shapes a companies endpoint can return:
    a dict with a companies[] list (the documented /api/marketmap shape),
    or a bare list of companies."""
    if isinstance(payload, dict):
        companies = payload.get("companies")
        if companies is None and isinstance(payload.get("results"), list):
            companies = payload["results"]
        return {
            "companies": companies or [],
            "total_companies": payload.get("total_companies") or payload.get("totalCompanies")
                                or payload.get("company_count") or payload.get("companyCount"),
            "segments": payload.get("segments") or [],
            "returned": payload.get("returned"),
            "capped": payload.get("capped"),
            "note": payload.get("note"),
        }
    if isinstance(payload, list):
        return {"companies": payload, "total_companies": None, "segments": [],
                "returned": len(payload), "capped": None, "note": None}
    return None


def _detail_urls_for(candidate: dict) -> list[str]:
    """The API's own companiesUrl first (documented field on every search
    result), then our constructed /api/marketmap?id= as a fallback.
    Deduplicated, order preserved."""
    urls = []
    own = candidate.get("companiesUrl") or candidate.get("companies_url")
    if own:
        if own.startswith("/"):
            own = "https://dealroom.co" + own
        urls.append(own)
    map_id = candidate.get("id")
    if map_id:
        urls.append(f"{DEALROOM_MARKETMAP_URL}?id={map_id}")
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


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

    Access strategy, in order, per candidate (broadest first):
      1. The candidate's own `companiesUrl` (a documented field on every
         search result) — the API's link, not one we construct.
      2. Our constructed /api/marketmap?id= (works for numeric
         "landscape-XXXXX" maps).
      3. If both fail and the map is a custom-slug/partner-built one,
         look it up in the documented /api/third-party-maps catalogue
         and log its company_count (funding sample unavailable there).
    Confirmed live that every prior 404 was a custom-slug map
    ("terrestrial-ai-compute", "market-intelligence-landscape",
    "us-novel-ai-startups"...) — the shape of partner-built maps, which
    is exactly what /api/third-party-maps exists to serve.
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

    results = search_resp.json().get("results", [])
    print(f"[diagnostic] tag={tag}: {len(results)} candidate map(s) — "
          f"{[(r.get('title'), r.get('companyCount')) for r in results]}")
    if not results:
        print(f"[warn] No market maps found for tag={tag}", file=sys.stderr)
        return None

    ranked = sorted(results, key=lambda r: r.get("companyCount", 0) or 0, reverse=True)

    # Pass 1 + 2: try each candidate's companiesUrl, then constructed URL.
    for candidate in ranked:
        map_id = candidate.get("id")
        for url in _detail_urls_for(candidate):
            try:
                detail_resp = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            except requests.RequestException as exc:
                print(f"[warn] detail fetch error tag={tag} url={url}: {exc}", file=sys.stderr)
                continue
            if detail_resp.status_code != 200:
                print(f"[diagnostic]   {url} -> HTTP {detail_resp.status_code}", file=sys.stderr)
                continue
            try:
                parsed = _parse_detail(detail_resp.json())
            except ValueError:
                print(f"[diagnostic]   {url} -> non-JSON response", file=sys.stderr)
                continue
            if not parsed:
                continue

            companies = parsed["companies"]
            sample_funding = sum(
                (c.get("totalFunding") or {}).get("amount", 0) or 0
                for c in companies if isinstance(c, dict)
            ) or None
            print(f"  tag={tag}: using '{candidate.get('title')}' via {url}")
            print(f"[diagnostic]   returned={parsed['returned']}, capped={parsed['capped']}, "
                  f"note={parsed['note']}")
            return {
                "tag": tag,
                "market_map_id": map_id,
                "market_map_title": candidate.get("title"),
                "total_companies": parsed["total_companies"] or candidate.get("companyCount"),
                "segment_count": len(parsed["segments"]),
                "sample_funding_usd": sample_funding,
                "sample_size": len(companies),
                "is_capped": parsed["capped"],
                "access_path": "companiesUrl" if url == _detail_urls_for(candidate)[0] and (candidate.get("companiesUrl") or candidate.get("companies_url")) else "marketmap",
                "source_label": candidate.get("source"),
                "dealroom_tags": candidate.get("tags") or [],
            }

    # Pass 3: partner-built maps via the documented third-party catalogue.
    catalogue = load_third_party_maps()
    for candidate in ranked:
        map_id = candidate.get("id") or ""
        title = (candidate.get("title") or "").strip().lower()
        rec = catalogue.get(map_id) or catalogue.get(title)
        if rec:
            count = rec.get("company_count") or rec.get("companyCount") or candidate.get("companyCount")
            print(f"  tag={tag}: '{candidate.get('title')}' found in third-party-maps "
                  f"(company_count={count}, source={rec.get('source_label')}) — count only, no funding sample")
            return {
                "tag": tag,
                "market_map_id": map_id,
                "market_map_title": candidate.get("title"),
                "total_companies": count,
                "segment_count": rec.get("category_count"),
                "sample_funding_usd": None,
                "sample_size": 0,
                "is_capped": None,
                "access_path": "third_party_maps",
                "source_label": rec.get("source_label"),
                "dealroom_tags": candidate.get("tags") or rec.get("tags") or [],
            }

    # Everything failed — dump the raw records so the next fix is made
    # from real URLs, not hypotheses.
    print(f"[warn] tag={tag}: every candidate failed on every access path. Raw candidates:", file=sys.stderr)
    for candidate in ranked:
        print(f"[raw]   {candidate}", file=sys.stderr)
    return None


def _write_rows_idempotent(rows: list[dict], today: str) -> None:
    """Write today's rows so that (a) re-running on the same UTC day
    REPLACES that day's rows instead of stacking duplicates — the same
    idempotency the GPU pricing pipelines have — and (b) if the existing
    file's header doesn't match FIELDNAMES (schema changed between
    versions), the old file is rotated to a .bak rather than corrupted
    by appending mismatched rows under a stale header. Nothing is ever
    deleted."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if SNAPSHOT_PATH.exists():
        with open(SNAPSHOT_PATH, newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != FIELDNAMES:
                backup = SNAPSHOT_PATH.with_name(
                    f"{SNAPSHOT_PATH.stem}.schema-v-old.{today}.bak.csv")
                SNAPSHOT_PATH.rename(backup)
                print(f"[diagnostic] header mismatch — rotated old file to {backup.name} (kept, not deleted)")
            else:
                existing = [r for r in reader if not (r.get("collected_at") or "").startswith(today)]
                dropped = sum(1 for _ in open(SNAPSHOT_PATH)) - 1 - len(existing)
                if dropped > 0:
                    print(f"[diagnostic] replacing {dropped} existing row(s) from {today} with this run's")

    with open(SNAPSHOT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(existing)
        writer.writerows(rows)


def main():
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    tags = discover_available_tags()
    print(f"Tracking {len(tags)} tag(s) this run: {tags}")

    # One row per MAP, not per tag: several tag queries can select the
    # same map ("1.5k+ AI Agents" under both AI and Global). Merge them.
    by_map: dict[str, dict] = {}
    for tag in tags:
        print(f"\nFetching Dealroom snapshot for tag={tag}...")
        snap = fetch_market_snapshot_for_tag(tag)
        if not snap:
            print(f"  No snapshot logged for tag={tag} this run — see warnings above.")
            continue
        key = snap["market_map_id"]
        if key in by_map:
            by_map[key]["_selected_for"].add(tag)
        else:
            snap["_selected_for"] = {tag}
            by_map[key] = snap

    if not by_map:
        sys.exit("No snapshots could be built for any tag this run — see warnings above.")

    rows = []
    for snap in by_map.values():
        dr_tags = snap.get("dealroom_tags") or []
        if isinstance(dr_tags, str):
            dr_tags = [dr_tags]
        rows.append({
            "collected_at": now.isoformat(),
            "source": "dealroom",
            "market_map_id": snap["market_map_id"],
            "market_map_title": snap["market_map_title"],
            "selected_for_tags": ";".join(sorted(snap["_selected_for"])),
            "dealroom_tags": ";".join(sorted(str(t) for t in dr_tags)),
            "total_companies": snap["total_companies"],
            "sample_funding_usd": snap["sample_funding_usd"],
            "sample_size": snap["sample_size"],
            "is_capped": snap["is_capped"],
            "segment_count": snap["segment_count"],
            "access_path": snap["access_path"],
            "source_label": snap["source_label"],
        })
    rows.sort(key=lambda r: r["market_map_title"] or "")

    _write_rows_idempotent(rows, today)
    print(f"\nLogged {len(rows)} unique map(s) covering {sum(len(s['_selected_for']) for s in by_map.values())} "
          f"tag selection(s) to {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
