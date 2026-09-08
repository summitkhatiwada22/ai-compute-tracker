#!/usr/bin/env python3
"""
fetch_capex.py

Pulls hyperscaler/neocloud financial ACTUALS (not guidance) from SEC
EDGAR's structured XBRL data — capex, revenue, and operating cash flow
for six companies, logged into ONE persistent file:
data/raw/capex/capex_financials_log.csv (renamed from capex_log.csv now
that it covers three metrics, not just capex).

COMPANIES (see COMPANIES below for CIKs/tags):
  - Amazon, Microsoft, Alphabet, Meta — the standard 4-hyperscaler set
  - Oracle — added because every independent methodology checked while
    building this (not just one source) tracks Oracle alongside the
    big 4 as a standard 5th hyperscaler-capex company
  - CoreWeave — added because it IPO'd in March 2025 and now files real
    10-Qs with the SEC (confirmed directly), making its capex trackable
    for the first time; it's the neocloud Microsoft reportedly drives
    ~72% of revenue for, directly relevant to this tracker's own
    hyperscaler/neocloud/marketplace tier structure
  NOT included, deliberately:
  - SpaceX: private, no SEC filings exist — a hard technical wall
  - Netflix: runs mostly on AWS rather than its own AI infrastructure
  - Tesla: capex dominated by manufacturing/Gigafactories, no clean way
    to isolate an AI-specific portion
  - NVIDIA: plays a different economic role (supplier, not buyer) —
    its capex is small/irrelevant here; its REVENUE would be the
    meaningful number for a *different* series (who's buying the chips)
  - Nebius: public (NASDAQ: NBIS) but a foreign private issuer, filing
    Form 20-F/6-K rather than 10-K/10-Q under different (IFRS-based)
    tagging conventions — a real candidate, but needs a separate
    pipeline, not force-fit into this one

METRICS TRACKED PER COMPANY:
  - capex: company-specific tag (see below — Amazon differs from the rest)
  - revenue: RevenueFromContractWithCustomerExcludingAssessedTax
  - operating_cash_flow: NetCashProvidedByUsedInOperatingActivities

CAPEX TAG NOTE — confirmed from multiple independent sources, not
guessed: Microsoft, Alphabet, Meta, Oracle all tag capex as
us-gaap:PaymentsToAcquirePropertyPlantAndEquipment. Amazon uses a
DIFFERENT tag: us-gaap:PaymentsToAcquireProductiveAssets (a real
methodological difference in how Amazon's own filings define the line
item, not a bug). CoreWeave is newer/smaller and untested here — it's
given the standard tag as a best guess; the [diagnostic] output on each
run will show 0 disclosed values if that guess is wrong, which is safe
(no crash, just an empty result for that one row) and immediately
visible rather than silently wrong.

METHODOLOGY NOTE — READ BEFORE TRUSTING THIS DATA: this is TOTAL
capex/revenue/operating cash flow, NOT an "AI-specific" breakout. No
such breakout exists anywhere in structured SEC filing data — companies
don't disclose it as a distinct GAAP line item. This is used as a PROXY
on the reasoning that most of the recent GROWTH in these numbers is
understood to be AI-driven, the same kind of structurally-undisclosed
limitation already documented for GPU pricing (hyperscaler-negotiated
bulk pricing) elsewhere in this tracker. State this plainly in any
published methodology page — don't imply this is AI-only spend.

HISTORY NOTE: no date filtering is applied anywhere below — every run
pulls the FULL available history SEC has for each tag, which for the
five established filers typically reaches back to ~2009 (when SEC's
XBRL mandate began). This is deliberate: capex growth was roughly FLAT
through 2023 (+2% YoY) and only visibly inflected around Q2 2023 (~2
quarters after ChatGPT's Nov 2022 launch) — you need the pre-2023
baseline in the data to actually see that inflection, not just take it
on faith. CoreWeave's history only goes back to its March 2025 IPO,
since it didn't publicly file before then.

PERIOD NOTE: cash-flow-statement figures in a 10-Q are often reported
as YEAR-TO-DATE CUMULATIVE, not just the single quarter (e.g. a Q3
filing may report "nine months ended," not just Q3 alone). This script
stores the exact period_start/period_end for every row rather than
assuming a fixed quarter length, so cumulative vs. discrete periods
are fully distinguishable later by their actual date span.

DESIGN NOTE: writes to ONE persistent, append-only log file, not a new
dated snapshot per run — each run fetches full available history,
compares against what's already logged (by accession number + period +
metric), and appends only genuinely NEW rows.

Runs DAILY (see the matching GitHub Actions workflow) — cheap, static
API calls, so no cost to checking daily even though real filings only
land a few times a year per company.

    export SEC_USER_AGENT="Your Name your-email@example.com"
    python scripts/fetch_capex.py

SEC requires a descriptive User-Agent (name + email) per their
fair-access policy — not a secret, just an identifying string.
"""

import csv
import os
import sys
from pathlib import Path

import requests

STANDARD_TAGS = {
    "revenue": "RevenueFromContractWithCustomerExcludingAssessedTax",
    "operating_cash_flow": "NetCashProvidedByUsedInOperatingActivities",
}

COMPANIES = [
    {
        "name": "Amazon", "cik": 1018724,
        "tags": {"capex": "PaymentsToAcquireProductiveAssets", **STANDARD_TAGS},
    },
    {
        "name": "Microsoft", "cik": 789019,
        "tags": {"capex": "PaymentsToAcquirePropertyPlantAndEquipment", **STANDARD_TAGS},
    },
    {
        "name": "Alphabet", "cik": 1652044,
        "tags": {"capex": "PaymentsToAcquirePropertyPlantAndEquipment", **STANDARD_TAGS},
    },
    {
        "name": "Meta", "cik": 1326801,
        "tags": {"capex": "PaymentsToAcquirePropertyPlantAndEquipment", **STANDARD_TAGS},
    },
    {
        "name": "Oracle", "cik": 1341439,
        "tags": {"capex": "PaymentsToAcquirePropertyPlantAndEquipment", **STANDARD_TAGS},
    },
    {
        "name": "CoreWeave", "cik": 1769628,
        # Standard tag used as a best guess — newer/smaller filer,
        # unverified live. Check the [diagnostic] output on first run.
        "tags": {"capex": "PaymentsToAcquirePropertyPlantAndEquipment", **STANDARD_TAGS},
    },
]

SEC_BASE = "https://data.sec.gov"
REQUEST_TIMEOUT_SECONDS = 30

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "capex"
LOG_PATH = OUTPUT_DIR / "capex_financials_log.csv"

FIELDNAMES = [
    "company", "cik", "metric", "tag", "period_start", "period_end",
    "fiscal_year", "fiscal_period", "form", "filed_date",
    "value_usd", "accession_number", "source_url",
]


def fetch_concept(cik: int, tag: str, user_agent: str) -> dict:
    """Call SEC's companyconcept endpoint for one company + one XBRL tag."""
    cik_padded = str(cik).zfill(10)
    url = f"{SEC_BASE}/api/xbrl/companyconcept/CIK{cik_padded}/us-gaap/{tag}.json"
    try:
        response = requests.get(
            url,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        print(f"[warn] request failed for CIK {cik} tag {tag}: {exc}", file=sys.stderr)
        return {}

    if response.status_code == 404:
        return {}  # this company simply doesn't use this tag — not an error
    if response.status_code != 200:
        print(f"[warn] HTTP {response.status_code} for CIK {cik} tag {tag}: {response.text[:300]}", file=sys.stderr)
        return {}

    return response.json()


def build_filing_url(cik: int, accession_number: str) -> str:
    """Deterministic SEC EDGAR filing-index URL from an accession number."""
    accn_no_dashes = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_no_dashes}/{accession_number}-index.htm"


def load_existing_keys() -> set:
    """Read the existing log (if any) and return the set of keys already
    recorded, so this run only ever appends genuinely new disclosures."""
    if not LOG_PATH.exists():
        return set()
    existing = set()
    with open(LOG_PATH, newline="") as f:
        for row in csv.DictReader(f):
            existing.add((row["accession_number"], row["period_start"], row["period_end"], row["metric"]))
    return existing


def main():
    user_agent = os.environ.get("SEC_USER_AGENT")
    if not user_agent:
        sys.exit(
            "SEC_USER_AGENT environment variable not set. SEC requires a descriptive "
            "User-Agent (your name + email) per their fair-access policy — this is not "
            "a secret, just an identifying string, e.g. 'Summit Khatiwada summit@example.com'."
        )

    existing_keys = load_existing_keys()
    print(f"[diagnostic] {len(existing_keys)} disclosures already logged")

    new_rows = []
    for company in COMPANIES:
        for metric, tag in company["tags"].items():
            print(f"Fetching {company['name']} / {metric} (CIK {company['cik']}, tag {tag})...")
            data = fetch_concept(company["cik"], tag, user_agent)
            usd_facts = data.get("units", {}).get("USD", [])
            print(f"  received {len(usd_facts)} disclosed values")

            for fact in usd_facts:
                if fact.get("form") not in ("10-Q", "10-K"):
                    continue

                key = (fact.get("accn"), fact.get("start"), fact.get("end"), metric)
                if key in existing_keys:
                    continue  # already logged in a previous run

                new_rows.append(
                    {
                        "company": company["name"],
                        "cik": company["cik"],
                        "metric": metric,
                        "tag": tag,
                        "period_start": fact.get("start"),
                        "period_end": fact.get("end"),
                        "fiscal_year": fact.get("fy"),
                        "fiscal_period": fact.get("fp"),
                        "form": fact.get("form"),
                        "filed_date": fact.get("filed"),
                        "value_usd": fact.get("val"),
                        "accession_number": fact.get("accn"),
                        "source_url": build_filing_url(company["cik"], fact.get("accn", "")),
                    }
                )
                existing_keys.add(key)

    if not new_rows:
        print("No new disclosures found since the last run — log already up to date.")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = LOG_PATH.exists()
    with open(LOG_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerows(new_rows)

    print(f"Appended {len(new_rows)} new disclosures to {LOG_PATH}")


if __name__ == "__main__":
    main()
