"""Build a three-statement model and DCF from SEC filings, with sensitivity tables.

    python main.py AAPL
    python main.py PLTR                                   # growth profile picked automatically
    python main.py MSFT --beta 1.0 --terminal-growth 0.03 --years 7
    python main.py --input inputs/ANT.json --assumptions inputs/ANT_assumptions.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from dataclasses import fields, replace
from datetime import date
from pathlib import Path

import pandas as pd

import edgar
import manual
import market
import model

PCT_ROWS = {"Revenue growth", "EBIT margin"}


def fmt(df: pd.DataFrame) -> pd.DataFrame:
    out = df.astype(object).copy()
    for row in df.index:
        for col in df.columns:
            v = df.at[row, col]
            if pd.isna(v):
                out.at[row, col] = "-"
            elif row in PCT_ROWS:
                out.at[row, col] = f"{v:.1%}"
            elif row == "Discount factor":
                out.at[row, col] = f"{v:.4f}"
            elif row == "Diluted EPS":
                out.at[row, col] = f"{v:,.2f}"
            elif abs(v) < 0.5:
                out.at[row, col] = "0"
            else:
                out.at[row, col] = f"({-v:,.0f})" if v < 0 else f"{v:,.0f}"
    return out


def fmt_value(df: pd.DataFrame, price: float | None, equity_mode: bool) -> pd.DataFrame:
    def cell(v):
        if pd.isna(v):
            return "n/a"
        if equity_mode:
            return f"{v:,.0f}"
        return f"{v:,.2f}" + (f" ({v / price - 1:+.0%})" if price else "")
    return df.apply(lambda col: col.map(cell))


def section(title: str) -> str:
    return f"\n{'=' * 100}\n{title}\n{'=' * 100}\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticker", nargs="?", help="US-listed ticker (data from SEC XBRL filings)")
    ap.add_argument("--input", type=Path, help="hand-entered financials JSON instead of a ticker (see inputs/)")
    ap.add_argument("--history", type=int, default=5, help="fiscal years of history to pull (default 5)")
    ap.add_argument("--no-ltm", action="store_true", help="don't add an LTM column from the latest 10-Q")
    ap.add_argument("--profile", choices=["stable", "growth"], help="default: growth if 3-yr revenue CAGR > 20%%")
    ap.add_argument("--years", type=int, help="projection years (default 5 stable / 10 growth)")
    ap.add_argument("--price", type=float, help="share price override")
    ap.add_argument("--shares", type=float, help="diluted shares outstanding, millions")
    ap.add_argument("--beta", type=float, help="equity beta override")
    ap.add_argument("--rf", type=float, help="risk-free rate, e.g. 0.0425")
    ap.add_argument("--erp", type=float, help="equity risk premium, e.g. 0.05")
    ap.add_argument("--terminal-growth", type=float)
    ap.add_argument("--exit-multiple", type=float, help="exit EV/EBITDA multiple")
    ap.add_argument("--assumptions", type=Path, help="JSON file of Assumptions fields to override")
    ap.add_argument("--no-market", action="store_true", help="skip fetching price / beta")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "output")
    args = ap.parse_args()
    if not args.ticker and not args.input:
        ap.error("give a ticker or --input")

    # ---- Data ---------------------------------------------------------------
    meta: dict = {}
    if args.input:
        raw, meta = manual.load_manual(args.input)
        ticker, cik, name = meta["ticker"], meta.get("cik"), meta["name"]
        source = f"{meta['source']['document']} filed {meta['source']['filed']} (hand-entered: {args.input.name})"
        shares = meta.get("shares_outstanding")
        shares_note = "from input file"
    else:
        ticker = args.ticker.upper()
        cik, name = edgar.lookup_company(ticker)
        facts = edgar.company_facts(cik)
        raw = edgar.annual_financials(facts, args.history, ltm=not args.no_ltm)
        source = "SEC EDGAR XBRL 10-K" + ("" if raw.columns[-1][:3] != "LTM" else " + latest 10-Q (LTM)") + " filings"
        shares_raw, shares_date = edgar.shares_outstanding(facts)
        shares = shares_raw / model.MM if shares_raw else None
        shares_note = f"cover page {shares_date}"
    hist = model.standardise(raw)
    if shares is None and not args.input:
        shares = hist.income.loc["Diluted shares"].iloc[-1]
        shares_note = "latest diluted weighted average"
    if args.shares:
        shares, shares_note = args.shares, "--shares"

    mkt = {}
    if not args.no_market and meta.get("trading", True):
        try:
            mkt = market.fetch_market_data(ticker)
        except Exception as exc:  # network / symbol issues shouldn't stop the model
            hist.warnings.append(f"Market data unavailable ({exc}); pass --price / --beta")
    price = args.price or mkt.get("price")
    market_cap = price * shares if price and shares else None

    # Without a share count, values are reported as total equity value in $bn.
    equity_mode = shares is None
    value_shares = 1000.0 if equity_mode else shares
    unit = "equity value, $bn" if equity_mode else "value per share, $"

    # ---- Assumptions --------------------------------------------------------
    a = model.derive_assumptions(hist, mkt, profile=args.profile)
    bridge = model.equity_bridge(hist)
    ltm_rev = hist.income.loc["Revenue"].iloc[-1]
    ebitda_ltm = hist.income.loc["EBITDA (memo)"].iloc[-1]
    ev_now = (market_cap + bridge["debt"] + bridge["nci"] - bridge["cash"] - bridge["lt_investments"]
              if market_cap else None)
    if ev_now and ebitda_ltm > 0 and a.profile == "stable":
        a.exit_multiple = round(min(max(ev_now / ebitda_ltm, 6.0), 25.0), 1)
    overrides = json.loads(args.assumptions.read_text(encoding="utf-8")) if args.assumptions else {}
    overrides = {k: v for k, v in overrides.items() if not k.startswith("_")}
    for key, val in {"projection_years": args.years, "beta": args.beta, "risk_free_rate": args.rf,
                     "equity_risk_premium": args.erp, "terminal_growth": args.terminal_growth,
                     "exit_multiple": args.exit_multiple}.items():
        if val is not None:
            overrides[key] = val
    unknown = set(overrides) - {f.name for f in fields(model.Assumptions)}
    if unknown:
        raise SystemExit(f"Unknown assumption(s): {', '.join(sorted(unknown))}")
    for key, val in overrides.items():
        setattr(a, key, val)

    # ---- Model --------------------------------------------------------------
    growth = a.profile == "growth"
    proj = model.project(hist, a)
    w = model.compute_wacc(a, market_cap, bridge["debt"])
    wacc = w["wacc"]
    gordon = model.dcf(proj, a, bridge, value_shares, wacc)
    multiple = model.dcf(proj, a, bridge, value_shares, wacc, exit_multiple=a.exit_multiple)
    step_w = 0.01 if growth else 0.005
    step_gm = 0.05 if growth else 0.02
    sens_wg = model.sensitivity_wacc_growth(proj, a, bridge, value_shares, wacc, step_w=step_w)
    sens_wm = model.sensitivity_wacc_multiple(proj, a, bridge, value_shares, wacc, step_w=step_w,
                                              step_m=4.0 if growth else 2.0)
    sens_gm = model.sensitivity_growth_margin(hist, a, bridge, value_shares, wacc, step_g=step_gm, step_m=step_gm)

    is_all = pd.concat([hist.income.drop(["Diluted shares", "Diluted EPS"]), proj.income], axis=1)
    bs_all = pd.concat([hist.balance, proj.balance], axis=1)
    cf_all = pd.concat([hist.cashflow, proj.cashflow], axis=1)
    dcf_tbl = proj.fcff.copy()
    dcf_tbl.loc["Discount factor"] = gordon["discount_factors"]
    dcf_tbl.loc["PV of unlevered FCF"] = dcf_tbl.loc["Unlevered FCF"] * gordon["discount_factors"]

    max_imbalance = proj.balance.loc["Balance check"].abs().max()
    if max_imbalance > 0.01:
        hist.warnings.append(f"Projected balance sheet out of balance by {max_imbalance:,.2f}")

    # Reverse DCF: what revenue growth is today's price paying for?
    reverse = None
    if price and not equity_mode:
        shift = model.implied_growth_shift(hist, a, bridge, value_shares, wacc, price)
        if shift is not None:
            p_impl = model.project(hist, replace(a, growth_shift=a.growth_shift + shift))
            rev_impl = p_impl.income.loc["Revenue"]
            n = len(rev_impl)
            reverse = {
                "growth_shift": shift,
                "year1_growth": float(p_impl.income.loc["Revenue growth"].iloc[0]),
                "final_year_revenue": float(rev_impl.iloc[-1]),
                "revenue_cagr": float((rev_impl.iloc[-1] / ltm_rev) ** (1 / n) - 1),
                "check_value": model.dcf(p_impl, a, bridge, value_shares, wacc)["per_share"],
            }

    # ---- Report -------------------------------------------------------------
    base_label = hist.income.columns[-1]
    base_end = hist.year_ends[-1] if hist.year_ends[-1][:2] != "FY" else base_label
    lines = [
        f"{name} ({ticker})" + (f"  |  CIK {cik}" if cik else "") + "  |  USD millions except per-share",
        f"Source: {source}",
        f"Profile: {a.profile} ({len(proj.income.columns)}-year projection)  |  "
        f"Valuation base period: {base_label}  |  run date {date.today()}",
    ]
    if shares:
        lines.append(f"Shares: {shares:,.1f}m ({shares_note})"
                     + (f"  |  Price: ${price:,.2f}  |  Market cap: ${market_cap:,.0f}m" if price else "  |  Price: n/a"))
    else:
        lines.append("Shares: not available - values shown as total equity value ($bn); pass --shares for per-share")
    if ev_now:
        lines.append(f"Market EV: ${ev_now:,.0f}m  =  {ev_now / ltm_rev:,.1f}x {base_label} revenue"
                     + (f",  {ev_now / ebitda_ltm:,.1f}x EBITDA" if ebitda_ltm > 0 else ""))
    for note in meta.get("notes", []):
        lines.append(f"  * {note}")

    lines += [section("INCOME STATEMENT"), fmt(is_all).to_string()]
    lines += [section("BALANCE SHEET"), fmt(bs_all).to_string()]
    lines += [section("CASH FLOW STATEMENT"), fmt(cf_all).to_string()]

    lines.append(section("KEY ASSUMPTIONS (derived from filings unless marked as override)"))
    for k, v in a.to_dict().items():
        tag = "  <- override" if k in overrides else ""
        if k in {"dso", "dio", "dpo"}:
            lines.append(f"  {k:<26}{v:>7.1f} days{tag}")
        elif k in {"beta", "exit_multiple"}:
            lines.append(f"  {k:<26}{v:>10.2f}{tag}")
        elif isinstance(v, list):
            lines.append(f"  {k:<26}{' '.join(f'{x:.1%}' for x in v)}  (replaces start/end/decay){tag}")
        elif isinstance(v, float):
            lines.append(f"  {k:<26}{v:>10.2%}{tag}")
        else:
            lines.append(f"  {k:<26}{v!s:>10}{tag}")
    if mkt.get("beta_raw") is not None and "beta" not in overrides:
        lines.append(f"  (beta: {mkt['months']}-month regression vs S&P 500, raw {mkt['beta_raw']:.2f}, Blume-adjusted)")

    lines.append(section("WACC"))
    lines += [
        f"  Cost of equity      {w['cost_of_equity']:.2%}  = {a.risk_free_rate:.2%} + {a.beta:.2f} x {a.equity_risk_premium:.2%}",
        f"  After-tax Kd        {w['after_tax_cost_of_debt']:.2%}  = {a.cost_of_debt:.2%} x (1 - {a.tax_rate:.1%})",
        f"  Weights (E / D)     {w['equity_weight']:.1%} / {w['debt_weight']:.1%}",
        f"  WACC                {wacc:.2%}",
    ]

    lines += [section("DISCOUNTED CASH FLOW"), fmt(dcf_tbl).to_string(), ""]
    rows = [("PV of projected FCF", "pv_fcf"), ("Terminal value", "terminal_value"),
            ("PV of terminal value", "pv_terminal_value"), ("Enterprise value", "enterprise_value")]
    lines.append(f"  {'':<34}{'Perpetuity growth':>20}{'Exit multiple':>20}")
    for label, key in rows:
        lines.append(f"  {label:<34}{gordon[key]:>20,.0f}{multiple[key]:>20,.0f}")
    for label, key in (("+ Cash & ST investments", "cash"), ("+ LT investments", "lt_investments"),
                       ("- Debt", "debt"), ("- Non-controlling interest", "nci")):
        lines.append(f"  {label:<34}{bridge[key]:>20,.0f}{bridge[key]:>20,.0f}")
    lines.append(f"  {'Equity value':<34}{gordon['equity_value']:>20,.0f}{multiple['equity_value']:>20,.0f}")
    if not equity_mode:
        lines.append(f"  {'Value per share ($)':<34}{gordon['per_share']:>20,.2f}{multiple['per_share']:>20,.2f}")
    if price and not equity_mode:
        lines.append(f"  {'Upside vs current price':<34}{gordon['per_share'] / price - 1:>20.1%}{multiple['per_share'] / price - 1:>20.1%}")
    lines.append(f"  {'EV / ' + str(base_label) + ' revenue':<34}{gordon['enterprise_value'] / ltm_rev:>19.1f}x{multiple['enterprise_value'] / ltm_rev:>19.1f}x")
    lines.append(f"  {'Terminal value % of EV':<34}{gordon['tv_share_of_ev']:>20.1%}{multiple['tv_share_of_ev']:>20.1%}")
    lines.append(f"  {'Implied exit EV/EBITDA':<34}{gordon['implied_exit_multiple']:>19.1f}x{a.exit_multiple:>19.1f}x")
    lines.append(f"  {'Implied perpetuity growth':<34}{a.terminal_growth:>20.2%}{multiple['implied_terminal_growth']:>20.2%}")

    if reverse:
        base_rev = proj.income.loc["Revenue"]
        n = len(base_rev)
        base_cagr = (base_rev.iloc[-1] / ltm_rev) ** (1 / n) - 1
        lines.append(section("REVERSE DCF: what the current price implies (perpetuity method, all else equal)"))
        lines += [
            f"  {'':<40}{'Base case':>16}{'Market-implied':>18}",
            f"  {'Year-1 revenue growth':<40}{proj.income.loc['Revenue growth'].iloc[0]:>16.1%}{reverse['year1_growth']:>18.1%}",
            f"  {f'{n}-yr revenue CAGR':<40}{base_cagr:>16.1%}{reverse['revenue_cagr']:>18.1%}",
            f"  {f'Year-{n} revenue ($m)':<40}{base_rev.iloc[-1]:>16,.0f}{reverse['final_year_revenue']:>18,.0f}",
            f"  {'Value per share ($)':<40}{gordon['per_share']:>16,.2f}{reverse['check_value']:>18,.2f}",
            f"  Growth path shifted by {reverse['growth_shift']:+.1%} every year to match ${price:,.2f}.",
        ]
    elif price and not equity_mode:
        lines += [section("REVERSE DCF"), "  No growth shift in [-50%, +150%] reproduces the current price."]

    pct_note = " (upside vs current price in brackets)" if price and not equity_mode else ""
    lines += [section(f"SENSITIVITY: {unit} - WACC vs terminal growth{pct_note}"),
              fmt_value(sens_wg, price, equity_mode).to_string()]
    lines += [section(f"SENSITIVITY: {unit} - WACC vs exit EV/EBITDA{pct_note}"),
              fmt_value(sens_wm, price, equity_mode).to_string()]
    lines += [section(f"SENSITIVITY: {unit} - revenue growth vs EBIT margin, perpetuity method{pct_note}"),
              fmt_value(sens_gm, price, equity_mode).to_string()]

    lines.append(section("NOTES & DATA WARNINGS"))
    lines += [
        "  - FCFF = EBIT - tax on positive EBIT + D&A - capex - change in NWC. SBC is a real cost (not added back).",
        "  - Operating leases are excluded from debt; 'Other' lines are residuals against reported subtotals.",
        "  - Interest is charged on opening balances (no circularity); a revolver keeps cash >= minimum cash.",
        f"  - Equity bridge uses the balance sheet at {base_end}; cash flows are discounted from that date.",
    ]
    lines += [f"  ! {msg}" for msg in hist.warnings]
    report = "\n".join(lines)
    print(report)

    # ---- Files --------------------------------------------------------------
    out = args.out / ticker
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.txt").write_text(report, encoding="utf-8")
    (out / "assumptions.json").write_text(json.dumps(a.to_dict(), indent=2))
    cfo, capex = hist.cashflow.loc["Cash from operations"].iloc[-1], -hist.cashflow.loc["Capital expenditure"].iloc[-1]
    summary = {
        "name": name, "ticker": ticker, "base_period": str(base_label), "profile": a.profile,
        "projection_years": len(proj.income.columns), "run_date": str(date.today()),
        "base_revenue": ltm_rev,
        "base_revenue_growth": model._latest_growth(hist),
        "base_ebit_margin": hist.income.loc["EBIT"].iloc[-1] / ltm_rev,
        "base_fcf_margin": (cfo - capex) / ltm_rev,
        "final_year_revenue": float(proj.income.loc["Revenue"].iloc[-1]),
        "final_year_ebit_margin": float(proj.income.loc["EBIT margin"].iloc[-1]),
        "wacc": wacc, "terminal_growth": a.terminal_growth, "exit_multiple": a.exit_multiple,
        "enterprise_value": gordon["enterprise_value"], "equity_value": gordon["equity_value"],
        "enterprise_value_exit": multiple["enterprise_value"], "equity_value_exit": multiple["equity_value"],
        "tv_share_of_ev": gordon["tv_share_of_ev"],
        "per_share": None if equity_mode else gordon["per_share"],
        "price": price, "market_cap": market_cap, "market_ev": ev_now,
        "implied_year1_growth": reverse and reverse["year1_growth"],
        "implied_revenue_cagr": reverse and reverse["revenue_cagr"],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    keys = [key for _, key in rows] + ["equity_value", "per_share", "tv_share_of_ev"]
    valuation = pd.DataFrame({"Perpetuity growth": {k: gordon[k] for k in keys},
                              "Exit multiple": {k: multiple[k] for k in keys}})
    sheets = {"Income statement": is_all, "Balance sheet": bs_all, "Cash flow": cf_all, "DCF": dcf_tbl,
              "Valuation": valuation, "Sens WACC x g": sens_wg, "Sens WACC x multiple": sens_wm,
              "Sens growth x margin": sens_gm, "Raw inputs": raw / model.MM}
    for sheet, df in sheets.items():
        df.to_csv(out / f"{sheet.lower().replace(' ', '_')}.csv")
    if importlib.util.find_spec("openpyxl"):
        with pd.ExcelWriter(out / f"{ticker}_model.xlsx") as xw:
            for sheet, df in sheets.items():
                df.to_excel(xw, sheet_name=sheet[:31])
    print(f"\nOutputs written to {out.resolve()}")


if __name__ == "__main__":
    main()
