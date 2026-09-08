# AI Compute Tracker

A public data tracker for the economics of the AI infrastructure buildout — part of an independent research practice covering AI capex, compute costs, and their intersection with monetary policy and capital markets.

## Series tracked

1. **Hyperscaler & neocloud capex, revenue, and operating cash flow** — quarterly and annual actuals for Amazon, Microsoft, Alphabet, Meta, Oracle, and CoreWeave, sourced directly from SEC EDGAR's structured XBRL filing data (10-Q/10-K). Covers capex, revenue, and operating cash flow — not forward-looking guidance from earnings calls, which is a separate, unstructured category not covered here. History reaches back to 2007–2013 depending on the company, through the present.
2. **GPU/compute pricing** — two tiers, both updated daily:
   - *Marketplace*: on-demand GPU rental pricing across every currently-listed model on Vast.ai, including VRAM, a performance score (dlperf), and performance-per-dollar.
   - *Neocloud*: on-demand GPU instance pricing from Lambda Cloud's full published catalog.
   - Hyperscaler-negotiated bulk GPU pricing is never publicly disclosed anywhere — this tracker shows the observable retail floor (marketplace) and published mid-tier (neocloud list prices), not the true top of market. The spread between the two tiers is treated as a meaningful data point in its own right, not a gap to apologize for.
3. **AI funding volume** — planned, not yet built. Will track venture funding into AI companies via Crunchbase News and the PitchBook-NVCA Venture Monitor.

## Status

- ✅ Series 1 (capex/financials) and Series 2 (GPU pricing, both tiers) are live, collecting real data on automated daily GitHub Actions schedules.
- 🚧 Series 3 (AI funding volume) not started.
- 🚧 Public methodology page and GitHub Pages dashboard not yet built.

## Methodology notes (full page coming with the dashboard)

- Capex/revenue/operating-cash-flow figures are **total** company-wide numbers, not an AI-specific breakout — no such breakout exists anywhere in structured filing data. Treated as a proxy on the reasoning that most recent growth in these numbers is AI-driven, not as literal AI-only spend.
- Cash-flow figures in quarterly filings are sometimes cumulative year-to-date rather than a single discrete quarter — each row's exact period is preserved rather than assumed, so this is recoverable at analysis time.
- GPU marketplace and neocloud prices are not directly comparable to undisclosed hyperscaler-negotiated bulk pricing — see above.

## License

Code: MIT. Data: CC-BY (attribution requested for reuse).
