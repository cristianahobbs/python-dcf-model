# python-dcf-model

A three-statement financial model and discounted cash flow (DCF) valuation engine in Python, built
directly from SEC filings. Give it a ticker and it pulls the company's 10-K/10-Q data from EDGAR, builds
linked income statement / balance sheet / cash flow projections, values the business two ways, runs
sensitivity tables, and solves a **reverse DCF**: what growth is today's share price paying for?

It also handles companies with no machine-readable data. Anthropic's 2026 S-1 prospectus is included
as a worked example of valuing a pre-IPO company from hand-transcribed filings.

## Findings

Market data as of 5 November 2026. Full reports are in [`examples/`](examples/).

| | Palantir (PLTR) | Anthropic (pre-IPO) | Apple (AAPL) |
|---|---|---|---|
| Revenue, last 12 months | $7.3B | $35.3B | $478.8B |
| Revenue growth | +92% | +1,206% (9M YoY) | +15% |
| Operating margin | 45.6% | −11.6% | 33.0% |
| DCF value (perpetuity method) | **$95 / share** | **$668B equity** | **$168 / share** |
| Share price | $155 | not yet trading | $345 |
| Market-implied revenue CAGR | 36% for 10 years | – | 24% for 5 years |

- **Palantir**: even at a 45.6% operating margin held for a decade, the price requires revenue to
  compound at ~36% a year to about $159B by 2036, versus ~28% (to $84B) in the base case, which already
  assumes growth decays from 92%.
- **Anthropic**: revenue went from $41M (2023) to a $68B annualised run rate (Q3 2026). The base case
  ($668B equity, 15x LTM revenue) assumes growth fading from 140% to 4.5% and margins expanding to 30%.
  An exit multiple of 20x EBITDA gives $1.3T; [the sensitivity tables](examples/ANT_report.txt) show the
  range.
- **Apple**: a textbook mature-company DCF at a 9.4% WACC gives roughly half the share price. The reverse
  DCF puts a number on the gap: ~24% annual growth, versus ~8% over the last three years and ~6% in the
  base case.

These are model outputs under stated assumptions, not investment advice.

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

## Project structure

```
edgar.py        SEC EDGAR client: XBRL tag mapping, annual + LTM extraction, caching
manual.py       loader for hand-entered filings (S-1s), LTM from interim periods
market.py       share price and regression beta (Yahoo Finance chart endpoint)
model.py        standardisation, assumptions, linked projection, DCF, sensitivities, reverse DCF
main.py         CLI and report writer (text, CSV, optional Excel)
compare.py      side-by-side comparison of completed runs
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
