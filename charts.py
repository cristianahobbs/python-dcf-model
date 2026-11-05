"""Render README charts (light + dark PNGs) from completed runs in output/.

    python charts.py --ranges PLTR AAPL ANT --heatmap ANT --bridge AAPL
"""
from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

ROOT = Path(__file__).parent
OUT = ROOT / "output"
CHARTS = ROOT / "docs" / "charts"

THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
              "grid": "#e1e0d9", "axis": "#c3c2b7", "s1": "#2a78d6", "s2": "#eb6834", "total": "#898781"},
    "dark": {"surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
             "grid": "#2c2c2a", "axis": "#383835", "s1": "#3987e5", "s2": "#d95926", "total": "#6b6a66"},
}
BLUE_RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
             "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]

plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"], "font.size": 10})


def load(ticker: str) -> tuple[dict, dict[str, pd.DataFrame]]:
    d = OUT / ticker
    summary = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    sens = {name: pd.read_csv(d / f"{name}.csv", index_col=0)
            for name in ("sens_wacc_x_g", "sens_wacc_x_multiple", "sens_growth_x_margin")}
    return summary, sens


def style_axes(ax, t: dict) -> None:
    ax.set_facecolor(t["surface"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["axis"])
    ax.tick_params(colors=t["muted"], length=0)
    ax.grid(axis="x", color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)


def save(fig, name: str, theme: str) -> None:
    CHARTS.mkdir(parents=True, exist_ok=True)
    suffix = "" if theme == "light" else "-dark"
    fig.savefig(CHARTS / f"{name}{suffix}.png", dpi=200, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
def valuation_ranges(tickers: list[str], theme: str) -> None:
    """Football field: range of each sensitivity table, base case dot, share price line."""
    t = THEMES[theme]
    rows = [("sens_wacc_x_g", "Perpetuity growth\nWACC × terminal growth"),
            ("sens_wacc_x_multiple", "Exit multiple\nWACC × EV/EBITDA"),
            ("sens_growth_x_margin", "Operating case\ngrowth × margin")]
    fig, axes = plt.subplots(len(tickers), 1, figsize=(8.6, 2.15 * len(tickers) + 0.5),
                             facecolor=t["surface"])
    axes = np.atleast_1d(axes)
    for ax, ticker in zip(axes, tickers):
        s, sens = load(ticker)
        per_share = s["per_share"] is not None
        style_axes(ax, t)
        for i, (key, label) in enumerate(rows):
            vals = sens[key].to_numpy(dtype=float)
            lo, hi = np.nanmin(vals), np.nanmax(vals)
            mid = vals[vals.shape[0] // 2, vals.shape[1] // 2]
            y = len(rows) - 1 - i
            ax.plot([lo, hi], [y, y], color=t["s1"], linewidth=9, solid_capstyle="round", alpha=0.35)
            ax.plot([mid], [y], "o", color=t["s1"], markersize=8, markeredgecolor=t["surface"], markeredgewidth=2)
            fmt = (lambda v: f"${v:,.0f}") if per_share else (lambda v: f"${v:,.0f}B")
            ax.annotate(fmt(lo), (lo, y), xytext=(-9, 0), textcoords="offset points", color=t["muted"],
                        ha="right", va="center", fontsize=8.5)
            ax.annotate(fmt(hi), (hi, y), xytext=(9, 0), textcoords="offset points", color=t["muted"],
                        ha="left", va="center", fontsize=8.5)
            ax.text(mid, y - 0.36, fmt(mid), color=t["ink2"], ha="center", va="top", fontsize=8.5,
                    fontweight="bold", zorder=5,
                    bbox={"facecolor": t["surface"], "edgecolor": "none", "pad": 1.5})
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([r[1] for r in reversed(rows)], color=t["ink2"], fontsize=8.5)
        ax.set_ylim(-0.75, len(rows) - 0.25)
        if s["price"] and per_share:
            ax.axvline(s["price"], color=t["ink"], linewidth=1.5)
            ax.text(s["price"], len(rows) - 0.2, f" share price ${s['price']:,.0f}", color=t["ink"],
                    fontsize=8.5, va="bottom", ha="left", fontweight="bold")
        unit = "value per share" if per_share else "equity value, not yet trading"
        ax.set_title(f"{s['name']}  ·  {unit}", loc="left", color=t["ink"], fontsize=10.5,
                     fontweight="bold", pad=14)
        ax.xaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter((lambda v, _: f"${v:,.0f}") if per_share else (lambda v, _: f"${v:,.0f}B")))
        lo_all = np.nanmin([np.nanmin(sens[k].to_numpy(dtype=float)) for k, _ in rows] + [s["price"] or np.inf])
        hi_all = np.nanmax([np.nanmax(sens[k].to_numpy(dtype=float)) for k, _ in rows] + [s["price"] or 0])
        pad = (hi_all - lo_all) * 0.13  # room for the end labels
        ax.set_xlim(max(lo_all - pad, 0), hi_all + pad)
    fig.text(0.01, -0.01, "Bars span each sensitivity table; dot = base case. Source: SEC filings, python-dcf-model.",
             color=t["muted"], fontsize=8)
    fig.tight_layout(h_pad=2.2)
    save(fig, "valuation_ranges", theme)


def heatmap(ticker: str, theme: str) -> None:
    """Revenue growth × terminal margin grid, sequential blue, base case outlined."""
    t = THEMES[theme]
    s, sens = load(ticker)
    grid = sens["sens_growth_x_margin"]
    vals = grid.to_numpy(dtype=float)
    per_share = s["per_share"] is not None
    ramp = BLUE_RAMP if theme == "light" else BLUE_RAMP[::-1]
    cmap = LinearSegmentedColormap.from_list("seq", ramp[1:-1] if theme == "light" else ramp[1:-1])
    fig, ax = plt.subplots(figsize=(7.6, 4.4), facecolor=t["surface"])
    ax.set_facecolor(t["surface"])
    ax.imshow(vals, cmap=cmap, aspect="auto")
    norm = (vals - np.nanmin(vals)) / (np.nanmax(vals) - np.nanmin(vals))
    for r in range(vals.shape[0]):
        for c in range(vals.shape[1]):
            dark_cell = norm[r, c] > 0.5 if theme == "light" else norm[r, c] < 0.5
            label = f"${vals[r, c]:,.0f}" if per_share else f"${vals[r, c]:,.0f}B"
            ax.text(c, r, label, ha="center", va="center", fontsize=9.5,
                    color="#fcfcfb" if dark_cell else "#0b0b0b",
                    fontweight="bold" if (r, c) == (vals.shape[0] // 2, vals.shape[1] // 2) else "normal")
    mr, mc = vals.shape[0] // 2, vals.shape[1] // 2
    ax.add_patch(plt.Rectangle((mc - 0.5, mr - 0.5), 1, 1, fill=False, edgecolor=t["ink"], linewidth=2))
    ax.set_xticks(range(len(grid.columns)))
    ax.set_xticklabels(grid.columns, color=t["ink2"])
    ax.set_yticks(range(len(grid.index)))
    ax.set_yticklabels([str(i).replace("->", "→") for i in grid.index], color=t["ink2"])
    ax.set_xlabel("Terminal operating margin", color=t["muted"])
    ax.set_ylabel("Revenue growth, year 1 → year 10", color=t["muted"])
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    unit = "value per share" if per_share else "equity value"
    ax.set_title(f"{s['name']}  ·  {unit} by growth and margin (outlined: base case)", loc="left",
                 color=t["ink"], fontsize=10.5, fontweight="bold", pad=12)
    fig.tight_layout()
    save(fig, f"{ticker.lower()}_growth_margin", theme)


def bridge(ticker: str, theme: str) -> None:
    """Waterfall from the base-case value to the share price."""
    t = THEMES[theme]
    s, _ = load(ticker)
    steps = s.get("value_bridge")
    if not steps:
        return
    labels = [textwrap.fill(st["label"], 14) for st in steps] + ["Share price"]
    details = [textwrap.fill(st["detail"].replace("->", "→"), 22) for st in steps] + [""]
    values = [st["value"] for st in steps]
    fig, ax = plt.subplots(figsize=(9.6, 4.8), facecolor=t["surface"])
    style_axes(ax, t)
    ax.grid(axis="y", color=t["grid"], linewidth=0.8)
    ax.grid(axis="x", visible=False)
    prev = 0.0
    for i, v in enumerate(values):
        if i == 0:
            bottom, height, color, text = 0, v, t["total"], f"${v:,.0f}"
        else:
            delta = v - prev
            bottom, height = min(prev, v), abs(delta)
            color = t["s2"] if steps[i]["label"] == "Faster revenue growth" else t["s1"]
            text = f"{'+' if delta >= 0 else '−'}${abs(delta):,.0f}"
        ax.bar(i, height, bottom=bottom, width=0.62, color=color, edgecolor=t["surface"], linewidth=2)
        ax.text(i, bottom + height + values[-1] * 0.015, text, ha="center", va="bottom",
                color=t["ink"], fontsize=9.5, fontweight="bold")
        prev = v
    n = len(values)
    ax.bar(n, s["price"], width=0.62, color=t["total"], edgecolor=t["surface"], linewidth=2)
    ax.text(n, s["price"] * 1.015, f"${s['price']:,.0f}", ha="center", va="bottom", color=t["ink"],
            fontsize=9.5, fontweight="bold")
    ax.set_xticks(range(n + 1))
    ax.set_xticklabels([f"{l}\n{d}" for l, d in zip(labels, details)], color=t["ink2"], fontsize=8)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_ylim(0, s["price"] * 1.12)
    ax.set_title(f"{s['name']}  ·  what it takes to get from the base case to the share price",
                 loc="left", color=t["ink"], fontsize=10.5, fontweight="bold", pad=12)
    fig.text(0.01, -0.02, "Each step keeps the ones before it. Blue: more generous assumptions. "
             "Orange: extra revenue growth solved for by the reverse DCF.", color=t["muted"], fontsize=8)
    fig.tight_layout()
    save(fig, f"{ticker.lower()}_bridge", theme)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ranges", nargs="*", default=[])
    ap.add_argument("--heatmap", nargs="*", default=[])
    ap.add_argument("--bridge", nargs="*", default=[])
    args = ap.parse_args()
    for theme in THEMES:
        if args.ranges:
            valuation_ranges([x.upper() for x in args.ranges], theme)
        for x in args.heatmap:
            heatmap(x.upper(), theme)
        for x in args.bridge:
            bridge(x.upper(), theme)
    print(f"Charts written to {CHARTS}")


if __name__ == "__main__":
    main()
