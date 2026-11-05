"""Market inputs that are not in SEC filings: share price and a regression beta.

Uses Yahoo Finance's public chart endpoint. Everything here is optional; the
CLI accepts --price / --beta overrides, and the model runs without it.
"""
from __future__ import annotations

import json
import urllib.request

import numpy as np


def _chart(symbol: str, range_: str = "5y", interval: str = "1mo") -> dict:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["chart"]["result"][0]


def _monthly_closes(result: dict) -> dict[int, float]:
    closes = result["indicators"].get("adjclose", [{}])[0].get("adjclose") or result["indicators"]["quote"][0]["close"]
    return {ts: c for ts, c in zip(result["timestamp"], closes) if c is not None}


def fetch_market_data(ticker: str, index: str = "^GSPC") -> dict:
    """Return {'price', 'beta_raw', 'beta', 'months'}; raises on network failure."""
    stock = _chart(ticker.upper().replace(".", "-"))
    price = float(stock["meta"]["regularMarketPrice"])
    s, m = _monthly_closes(stock), _monthly_closes(_chart(index))
    common = sorted(set(s) & set(m))
    out = {"price": price, "beta_raw": None, "beta": None, "months": len(common)}
    if len(common) >= 24:
        rs = np.diff(np.log([s[t] for t in common]))
        rm = np.diff(np.log([m[t] for t in common]))
        beta_raw = float(np.cov(rs, rm, ddof=1)[0, 1] / np.var(rm, ddof=1))
        out["beta_raw"] = beta_raw
        out["beta"] = 0.67 * beta_raw + 0.33  # Blume adjustment toward 1.0
    return out
