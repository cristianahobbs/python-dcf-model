"""Rewrite the generated README sections (Findings, Sanity check) from the latest runs in output/.

    python update_readme.py PLTR ANT AAPL --sanity AAPL
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).parent


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


def lower_first(label: str) -> str:
    """'Add back SBC' -> 'add back SBC'; leaves acronyms like 'WACC -1pt' alone."""
    return label if label[:2].isupper() else label[0].lower() + label[1:]


def sanity(s: dict) -> str:
    """The forecast-free check and the base-to-price bridge, in words."""
    chk, steps = s["sanity_check"], s["value_bridge"]
    loosened = [st for st in steps[1:] if st["label"] != "Faster revenue growth"]
    after = loosened[-1]["value"] if loosened else steps[0]["value"]
    lines = [
        f"**Forecast-free check.** At ${s['price']:,.0f}, {s['name']} trades at a {chk['fcf_yield']:.1%} "
        f"free-cash-flow yield ({s['base_period']} FCF {bn(chk['fcf'])} on a {bn(chk['market_ev'])} enterprise "
        f"value). At a {s['wacc']:.1%} WACC, that price only works if free cash flow grows "
        f"~{chk['implied_perpetual_growth']:.1%} a year forever, with no forecast involved. So the gap to the "
        f"DCF is not a modelling error: it comes from what the market expects.",
        "",
        f"**Bridge to the price.** Making every assumption more generous "
        f"({', '.join(lower_first(st['label']) for st in loosened)}) lifts the DCF from ${steps[0]['value']:,.0f} "
        f"to only ${after:,.0f}.",
    ]
    growth = next((st for st in steps if st["label"] == "Faster revenue growth"), None)
    if growth:
        lines[-1] += (f" Closing the rest needs average revenue growth of ~{growth['avg_growth_after']:.0%} a year "
                      f"for {s['projection_years']} years, versus {growth['avg_growth_before']:.1%} in the model.")
    return "\n".join(lines)


def replace_block(text: str, name: str, body: str) -> str:
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{start}\n{body}\n{end}{tail}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--sanity", help="ticker for the Sanity check section")
    args = ap.parse_args()
    load = lambda t: json.loads((ROOT / "output" / t.upper() / "summary.json").read_text(encoding="utf-8"))
    readme = ROOT / "README.md"
    text = replace_block(readme.read_text(encoding="utf-8"), "findings", findings([load(t) for t in args.tickers]))
    if args.sanity:
        text = replace_block(text, "sanity", sanity(load(args.sanity)))
    readme.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
