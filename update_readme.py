"""Rewrite the README "Findings" section from the latest runs in output/.

    python update_readme.py PLTR ANT AAPL
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).parent
START, END = "<!-- findings:start -->", "<!-- findings:end -->"


def bn(x: float) -> str:
    return f"${x / 1000:,.1f}B" if x < 1_000_000 else f"${x / 1_000_000:,.2f}T"


def value_cell(s: dict) -> str:
    if s["per_share"] is not None:
        return f"**${s['per_share']:,.0f} / share**"
    return f"**{bn(s['equity_value'])} equity**"


def bullet(s: dict) -> str:
    name, years = s["name"], s["projection_years"]
    if s["price"] and s["per_share"] is not None and s.get("implied_revenue_cagr") is not None:
        return (f"- **{name}**: the DCF gives ${s['per_share']:,.0f} a share against a ${s['price']:,.0f} price "
                f"(WACC {s['wacc']:.1%}). The reverse DCF says the price needs revenue to compound at "
                f"~{s['implied_revenue_cagr']:.0%} a year for {years} years, to about "
                f"{bn(s['implied_final_year_revenue'])}, versus ~{s['base_revenue_cagr']:.0%} "
                f"(to {bn(s['final_year_revenue'])}) in the base case, holding margins and the discount rate fixed.")
    return (f"- **{name}**: base case {bn(s['equity_value'])} equity value "
            f"({s['enterprise_value'] / s['base_revenue']:.0f}x {s['base_period']} revenue), assuming revenue "
            f"compounds at ~{s['base_revenue_cagr']:.0%} a year to {bn(s['final_year_revenue'])} with a "
            f"{s['final_year_ebit_margin']:.0%} operating margin by year {years}. An exit multiple of "
            f"{s['exit_multiple']:.0f}x EBITDA gives {bn(s['equity_value_exit'])}. Not trading yet, so there is "
            f"no market price to compare against.")


def findings(summaries: list[dict]) -> str:
    cols = " | ".join(f"{s['name']} ({s['ticker']})" for s in summaries)
    rows = [
        ("Revenue, " + " / ".join(sorted({s["base_period"] for s in summaries})),
         lambda s: bn(s["base_revenue"])),
        ("Revenue growth (latest year-on-year)",
         lambda s: f"{s['base_revenue_growth']:+,.0%}" if s["base_revenue_growth"] is not None else "–"),
        ("Operating margin", lambda s: f"{s['base_ebit_margin']:.1%}"),
        ("DCF value (perpetuity method)", value_cell),
        ("Share price", lambda s: f"${s['price']:,.2f}" if s["price"] else "not trading"),
        ("Market-implied revenue CAGR",
         lambda s: f"{s['implied_revenue_cagr']:.0%} for {s['projection_years']} years"
         if s.get("implied_revenue_cagr") is not None else "–"),
    ]
    lines = [
        f"Last updated **{max(s['run_date'] for s in summaries)}** by the weekly "
        f"[update job](.github/workflows/weekly-update.yml). Full reports are in [`examples/`](examples/).",
        "",
        f"| | {cols} |",
        "|---|" + "---|" * len(summaries),
    ]
    lines += [f"| {label} | " + " | ".join(fn(s) for s in summaries) + " |" for label, fn in rows]
    lines += [""] + [bullet(s) for s in summaries]
    lines += ["", "These are model outputs under stated assumptions, not investment advice."]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="+")
    args = ap.parse_args()
    summaries = [json.loads((ROOT / "output" / t.upper() / "summary.json").read_text(encoding="utf-8"))
                 for t in args.tickers]
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    readme.write_text(f"{head}{START}\n{findings(summaries)}\n{END}{tail}", encoding="utf-8")


if __name__ == "__main__":
    main()
