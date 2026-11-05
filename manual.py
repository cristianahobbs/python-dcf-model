"""Load hand-entered financials (e.g. from an S-1 prospectus that has no XBRL data).

The JSON file holds line items in USD millions keyed by the same names that
`edgar.annual_financials` produces, so the rest of the model runs unchanged.
See inputs/ANT.json for the format.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import edgar

ITEMS = [*edgar.DURATION_TAGS, *edgar.INSTANT_TAGS, *edgar.SHARE_TAGS]
FLOW_ITEMS = set(edgar.DURATION_TAGS)


def load_manual(path: Path) -> tuple[pd.DataFrame, dict]:
    """Return (raw DataFrame in USD / shares, metadata) for the periods listed in `history`."""
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    items: dict[str, dict[str, float | None]] = spec["items"]
    unknown = set(items) - set(ITEMS) - set(spec.get("reclassify_to_equity", []))
    if unknown:
        raise ValueError(f"Unknown line items in {path}: {', '.join(sorted(unknown))}")

    def value(item: str, period: str) -> float | None:
        return items.get(item, {}).get(period)

    columns = {}
    for period in spec["history"]:
        columns[period] = {item: value(item, period) for item in ITEMS}

    if ltm := spec.get("ltm"):
        col = {}
        for item in ITEMS:
            if item in FLOW_ITEMS:
                parts = [value(item, ltm[k]) for k in ("annual", "add", "subtract")]
                col[item] = None if None in parts else parts[0] + parts[1] - parts[2]
            else:  # balances and share counts: latest period
                col[item] = value(item, ltm["balance"])
        columns[ltm["label"]] = col

    # Instruments that convert into common stock at IPO (convertible notes, SAFEs) are
    # moved out of liabilities so the balance sheet and equity bridge are pro forma.
    for item in spec.get("reclassify_to_equity", []):
        for period, col in columns.items():
            src = ltm["balance"] if ltm and period == ltm["label"] else period
            amount = value(item, src)
            if amount and col.get("total_liabilities") is not None:
                col["total_liabilities"] -= amount

    raw = pd.DataFrame(columns).reindex(ITEMS).astype(float) * 1e6
    if (g := spec.get("latest_yoy_revenue_growth")) is not None:
        raw.attrs["latest_yoy_growth"] = g
    return raw, spec
