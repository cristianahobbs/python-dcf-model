"""Side-by-side comparison of companies already run through main.py.

    python compare.py PLTR ANT
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROWS = [
    ("Base period", "base_period", "{}"),
    ("Profile / projection years", None, None),
    ("Revenue, base period ($bn)", "base_revenue", "bn"),
    ("Revenue growth, latest YoY", "base_revenue_growth", "pct"),
    ("EBIT margin, base period", "base_ebit_margin", "pct"),
    ("FCF margin (CFO - capex), base period", "base_fcf_margin", "pct"),
    ("Final-year revenue ($bn)", "final_year_revenue", "bn"),
    ("Final-year EBIT margin", "final_year_ebit_margin", "pct"),
    ("WACC", "wacc", "pct"),
    ("DCF enterprise value, perpetuity ($bn)", "enterprise_value", "bn"),
    ("DCF equity value, perpetuity ($bn)", "equity_value", "bn"),
    ("DCF equity value, exit multiple ($bn)", "equity_value_exit", "bn"),
    ("DCF EV / base revenue", None, None),
    ("Terminal value % of EV", "tv_share_of_ev", "pct"),
    ("DCF value per share", "per_share", "usd"),
    ("Share price", "price", "usd"),
    ("Market cap ($bn)", "market_cap", "bn"),
    ("Market EV / base revenue", None, None),
    ("Market-implied year-1 growth", "implied_year1_growth", "pct"),
    ("Market-implied revenue CAGR", "implied_revenue_cagr", "pct"),
]


def cell(s: dict, label: str, key: str | None, kind: str | None) -> str:
    if label.startswith("Profile"):
        return f"{s['profile']} / {s['projection_years']}"
    if label == "DCF EV / base revenue":
        return f"{s['enterprise_value'] / s['base_revenue']:.1f}x"
    if label == "Market EV / base revenue":
        return f"{s['market_ev'] / s['base_revenue']:.1f}x" if s.get("market_ev") else "n/a"
    v = s.get(key)
    if v is None:
        return "n/a"
    if kind == "pct":
        return f"{v:.1%}"
    if kind == "bn":
        return f"{v / 1000:,.1f}"
    if kind == "usd":
        return f"${v:,.2f}"
    return str(v)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "output")
    args = ap.parse_args()

    summaries = []
    for t in args.tickers:
        path = args.out / t.upper() / "summary.json"
        if not path.exists():
            raise SystemExit(f"{path} not found - run main.py for {t} first")
        summaries.append(json.loads(path.read_text(encoding="utf-8")))

    width = max(len(r[0]) for r in ROWS) + 2
    col = max(16, *(len(s["name"]) + 2 for s in summaries))
    print(f"{'':<{width}}" + "".join(f"{s['name']:>{col}}" for s in summaries))
    for label, key, kind in ROWS:
        print(f"{label:<{width}}" + "".join(f"{cell(s, label, key, kind):>{col}}" for s in summaries))


if __name__ == "__main__":
    main()
