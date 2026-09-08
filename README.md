# AI Compute Tracker

A public data tracker for the economics of the AI infrastructure buildout — part of an independent research practice covering AI capex, compute costs, AI-sector funding, and their intersection with monetary policy and capital markets.

## Series tracked

1. **Hyperscaler & neocloud capex, revenue, and operating cash flow** — quarterly and annual actuals for Amazon, Microsoft, Alphabet, Meta, Oracle, and CoreWeave, sourced directly from SEC EDGAR's structured XBRL filing data (10-Q/10-K). Covers capex, revenue, and operating cash flow — not forward-looking guidance from earnings calls, which is a separate, unstructured category not covered here. History reaches back to 2007–2013 depending on the company, through the present. **Updates daily** (checks for new filings; most days are a no-op).
2. **GPU/compute pricing** — two tiers, both updated **daily**:
   - *Marketplace*: on-demand GPU rental pricing across every currently-listed model on Vast.ai, including VRAM, a performance score (dlperf), and performance-per-dollar.
   - *Neocloud*: on-demand GPU instance pricing from Lambda Cloud's full published catalog.
   - Hyperscaler-negotiated bulk GPU pricing is never publicly disclosed anywhere — this tracker shows the observable retail floor (marketplace) and published mid-tier (neocloud list prices), not the true top of market. The spread between the two tiers is treated as a meaningful data point in its own right.
3. **AI funding volume** — an aggregate snapshot (total companies, total funding, segment count) of Dealroom's curated AI sector market map, sourced from Dealroom's free public API. **Updates quarterly.** This reflects Dealroom's own private research and sector categorization, not an independently verifiable government figure — unlike the capex series.

## Status

✅ All three series are live, collecting real data on automated GitHub Actions schedules — no manual steps required going forward.

## Methodology notes (full page coming with the GitHub Pages dashboard)

- Capex/revenue/operating-cash-flow figures are **total** company-wide numbers, not an AI-specific breakout — no such breakout exists anywhere in structured filing data. Treated as a proxy on the reasoning that most recent growth in these numbers is AI-driven, not as literal AI-only spend.
- Cash-flow figures in quarterly filings are sometimes cumulative year-to-date rather than a single discrete quarter — each row's exact period is preserved rather than assumed, so this is recoverable at analysis time.
- GPU marketplace and neocloud prices are not directly comparable to undisclosed hyperscaler-negotiated bulk pricing — see above.
- The funding series is single-sourced (Dealroom only) by deliberate choice, to keep the pipeline simple and free. A SEC Form D cross-reference was considered and explicitly parked as a documented future fast-follow, not abandoned — see git history on `fetch_funding.py` for the reasoning.
- `total_companies` in the funding series is Dealroom's own reported count and can be trusted directly. `sample_funding_usd` is NOT a true sector total — Dealroom's public API only returns a capped sample of companies per list (`sample_size` shows exactly how many), so this figure is a partial, directional lower bound, not the real aggregate funding of the sector.

## Data pipeline

Each series has its own fetch script (`scripts/fetch_*.py`) and its own GitHub Actions workflow (`.github/workflows/fetch-*.yml`), writing raw CSV snapshots to `data/raw/`. `scripts/build_database.py` rebuilds a queryable DuckDB database (`data/tracker.duckdb`) from all raw files after every fetch — nothing is ever deleted, only re-derived from the full history of raw files each time.

## License

Code: MIT. Data: CC-BY (attribution requested for reuse).
