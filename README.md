# python-dcf-model

[![tests](https://github.com/cristianahobbs/python-dcf-model/actions/workflows/tests.yml/badge.svg)](https://github.com/cristianahobbs/python-dcf-model/actions/workflows/tests.yml)
[![weekly update](https://github.com/cristianahobbs/python-dcf-model/actions/workflows/weekly-update.yml/badge.svg)](https://github.com/cristianahobbs/python-dcf-model/actions/workflows/weekly-update.yml)

A three-statement financial model and discounted cash flow (DCF) valuation engine in Python, built
directly from SEC filings. Give it a ticker and it pulls the company's 10-K/10-Q data from EDGAR, builds
linked income statement / balance sheet / cash flow projections, values the business two ways, runs
sensitivity tables, and solves a **reverse DCF**: what growth is today's share price paying for?

It also handles companies with no machine-readable data. Anthropic's 2026 S-1 prospectus is included
as a worked example of valuing a pre-IPO company from hand-transcribed filings.

## Findings

<!-- findings:start -->
Last updated **2026-11-05** by the weekly [update job](.github/workflows/weekly-update.yml). Full reports are in [`examples/`](examples/).

| | Palantir Technologies Inc. (PLTR) | Anthropic, PBC (ANT) | Apple Inc. (AAPL) |
|---|---|---|---|
| Revenue, FY2026 / LTM 2026-09-30 | $7.3B | $35.3B | $478.8B |
| Revenue growth (latest year-on-year) | +92% | +1,206% | +15% |
| Operating margin | 45.6% | -11.6% | 33.0% |
| DCF value (perpetuity method) | **$95 / share** | **$668.4B equity** | **$168 / share** |
| Share price | $155.30 | not trading | $345.14 |
| Market-implied revenue CAGR | 36% for 10 years | – | 24% for 5 years |

- **Palantir Technologies Inc.**: the DCF gives $95 a share against a $155 price (WACC 11.2%). The reverse DCF says the price needs revenue to compound at ~36% a year for 10 years, to about $158.9B, versus ~28% (to $84.0B) in the base case, holding margins and the discount rate fixed.
- **Anthropic, PBC**: base case $668.4B equity value (15x LTM 2026-09-30 revenue), assuming revenue compounds at ~29% a year to $444.9B with a 30% operating margin by year 10. An exit multiple of 20x EBITDA gives $1.30T. Not trading yet, so there is no market price to compare against.
- **Apple Inc.**: the DCF gives $168 a share against a $345 price (WACC 9.4%). The reverse DCF says the price needs revenue to compound at ~24% a year for 5 years, to about $1.39T, versus ~6% (to $652.9B) in the base case, holding margins and the discount rate fixed.

These are model outputs under stated assumptions, not investment advice.
<!-- findings:end -->

## Quick start

```bash
pip install -r requirements.txt

python main.py AAPL                     # any US-listed ticker; mature companies get a 5-year model
python main.py PLTR                     # high-growth companies get a 10-year model automatically
python main.py --input inputs/ANT.json --assumptions inputs/ANT_assumptions.json   # from an S-1
python compare.py PLTR ANT AAPL         # side-by-side summary

python -m unittest discover -s tests    # offline test suite
```

Optional flags: `--price`, `--beta`, `--shares`, `--rf`, `--erp`, `--terminal-growth`, `--exit-multiple`,
`--years`, `--profile {stable,growth}`, `--assumptions file.json`. Run `python main.py -h` for the list.
The SEC asks API users to identify themselves: set `SEC_USER_AGENT="Your Name you@example.com"`.

## How it works

```
SEC EDGAR XBRL ──► edgar.py ──┐
  (10-K + latest 10-Q → LTM)  │
                              ├─► model.standardise ─► derive_assumptions ─► project ─► dcf ─► report
S-1 / manual JSON ─► manual.py┘     condensed IS/BS/CF    stable or growth      linked     + sensitivities
                                    tied to reported      profile               3 stmts    + reverse DCF
                                    subtotals
```

**Data.** `edgar.py` reads EDGAR's XBRL `companyfacts` API, maps US-GAAP tags to line items with per-year
fallbacks (companies rename tags over time), and keeps the latest-filed value so restatements flow
through. When a 10-Q post-dates the last 10-K it builds a last-twelve-months column
(FY + current YTD − prior YTD), so valuations start from the most recent data.

**Three statements.** The projection is fully linked: income statement → cash flow → balance sheet.
Working capital is driven by DSO/DIO/DPO, PP&E by a capex/D&A roll-forward, equity by net income + SBC −
dividends − buybacks, and a revolver draws when cash falls below a minimum and is repaid from surplus cash.
Interest is charged on opening balances to avoid circular references. Tests confirm the balance sheet
balances and cash reconciles every year.

**Assumptions.** Two profiles, chosen automatically from the 3-year revenue CAGR:

| | Stable | Growth (CAGR > 20%) |
|---|---|---|
| Horizon | 5 years | 10 years |
| Anchoring | 3-year averages | latest period |
| Revenue growth | linear fade | geometric fade to terminal + 2% |
| Tax | historical effective rate | ramps to 23% as tax losses are used up |
| Exit multiple | current EV/EBITDA (6–25x) | 20x (a mature multiple, not today's) |

Every driver can be overridden from a JSON file. Keys starting with `_` are treated as comments, so the
reasoning can live next to the numbers (see [`inputs/ANT_assumptions.json`](inputs/ANT_assumptions.json)).

**Valuation.** Unlevered free cash flow = EBIT − taxes on positive EBIT + D&A − capex − ΔNWC.
Stock-based compensation is treated as a real expense (not added back). The discount rate is a
CAPM-based WACC with market-value weights; beta comes from a 5-year monthly regression against the
S&P 500 (Blume-adjusted) when price history exists. Terminal value is computed by perpetuity growth and by
exit multiple, each showing what it implies about the other. Cash flows use the mid-year convention.

**Reverse DCF.** A bisection solver finds the parallel shift in the revenue growth path at which the
model's value equals the market price, then reports the implied growth rates and final-year revenue.

## The Anthropic case study

Anthropic filed its S-1 in October 2026 without XBRL financial data, so
[`inputs/ANT.json`](inputs/ANT.json) transcribes the audited and interim statements by hand, with page
references. Modelling decisions documented in that file:

- **Cost of revenue**: no gross profit is reported, so "compute and infrastructure" stands in, which
  overstates cost of revenue because it includes model-training compute.
- **Non-cash losses**: the $94B fair-value loss on convertible notes, derivatives and SAFEs in the first
  nine months of 2026 is non-operating, so it doesn't touch free cash flow.
- **Convertible notes**: these convert to equity at the IPO, so they are reclassified out of debt
  (pro forma).
- **Shares**: the share count and price range were blank in the filing, so value is reported as total
  equity.

## Automation

Two GitHub Actions workflows keep the repository current:

- [`tests.yml`](.github/workflows/tests.yml) runs the test suite on every push and pull request.
- [`weekly-update.yml`](.github/workflows/weekly-update.yml) runs every Monday: it re-values the example
  companies with fresh prices and any newly filed 10-Qs/10-Ks, regenerates `examples/` and the Findings
  section above (via `update_readme.py`), and commits the result. If market data is unavailable, it skips
  the commit rather than publishing reports without prices.

## Project structure

```
edgar.py        SEC EDGAR client: XBRL tag mapping, annual + LTM extraction, caching
manual.py       loader for hand-entered filings (S-1s), LTM from interim periods
market.py       share price and regression beta (Yahoo Finance chart endpoint)
model.py        standardisation, assumptions, linked projection, DCF, sensitivities, reverse DCF
main.py         CLI and report writer (text, CSV, optional Excel)
compare.py      side-by-side comparison of completed runs
update_readme.py  regenerates the README Findings section from the latest runs
.github/        CI: tests on every push, weekly model refresh
inputs/         hand-entered Anthropic S-1 data and base-case assumptions
examples/       sample reports (PLTR, ANT, AAPL) and comparison table
tests/          unittest suite (offline)
```

## Limitations

- US-GAAP filers only (no IFRS / 20-F). Banks and insurers don't suit a free-cash-flow model.
- Operating leases are excluded from debt.
- Market data comes from an unofficial Yahoo endpoint; `--price` and `--beta` override it.
- For young, fast-changing companies the result is driven by assumptions, not history. The sensitivity
  tables and reverse DCF exist to make that explicit.

## License

MIT. See [LICENSE](LICENSE).
