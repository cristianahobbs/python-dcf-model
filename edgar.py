"""Fetch and normalise annual financial data from SEC EDGAR XBRL "company facts".

Every number in the historical statements comes from the company's own 10-K
filings, as tagged in XBRL and published at https://data.sec.gov.
"""
from __future__ import annotations

import gzip
import json
import os
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# The SEC asks automated clients to identify themselves ("Name email").
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "python-dcf-model research admin@example.com")
CACHE_DIR = Path(__file__).parent / ".cache"
ANNUAL_FORMS = {"10-K", "10-K/A", "10-KT"}


def _fetch_json(url: str, cache_name: str, max_age_hours: float = 24) -> dict:
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / cache_name
    if path.exists() and time.time() - path.stat().st_mtime < max_age_hours * 3600:
        return json.loads(path.read_text(encoding="utf-8"))
    req = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    path.write_bytes(raw)
    return json.loads(raw)


def lookup_company(ticker: str) -> tuple[int, str]:
    """Return (CIK, company name) for a ticker."""
    data = _fetch_json("https://www.sec.gov/files/company_tickers.json", "company_tickers.json", 24 * 7)
    t = ticker.upper().replace(".", "-")
    for row in data.values():
        if row["ticker"].upper() == t:
            return int(row["cik_str"]), row["title"]
    raise ValueError(f"Ticker {ticker!r} not found in SEC company_tickers.json")


def company_facts(cik: int) -> dict:
    return _fetch_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", f"facts_{cik}.json")


# ---------------------------------------------------------------------------
# Tag map: line item -> candidate us-gaap tags in priority order. For each
# fiscal year the first tag with a value wins, which copes with companies
# switching tags over time (e.g. SalesRevenueNet -> RevenueFromContract...).
# ---------------------------------------------------------------------------
DURATION_TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
    "gross_profit": ["GrossProfit"],
    "rnd": ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "ebit": ["OperatingIncomeLoss"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "InterestPaidNet"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
           "DepreciationAndAmortization", "Depreciation"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsForCapitalImprovements"],
    "cfi": ["NetCashProvidedByUsedInInvestingActivities", "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"],
    "cff": ["NetCashProvidedByUsedInFinancingActivities", "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations"],
    "dividends": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
}

INSTANT_TAGS = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "st_investments": ["MarketableSecuritiesCurrent", "ShortTermInvestments", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "receivables": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
    "inventory": ["InventoryNet"],
    "current_assets": ["AssetsCurrent"],
    "ppe": ["PropertyPlantAndEquipmentNet",
            "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"],
    "lt_investments": ["MarketableSecuritiesNoncurrent", "LongTermInvestments", "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent"],
    "total_assets": ["Assets"],
    "payables": ["AccountsPayableCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "debt_current_total": ["DebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "ltd_current": ["LongTermDebtCurrent"],
    "commercial_paper": ["CommercialPaper"],
    "st_borrowings": ["ShortTermBorrowings"],
    "debt_noncurrent": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
    "total_liabilities": ["Liabilities"],
    "equity_parent": ["StockholdersEquity"],
    "equity_total": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "nci": ["MinorityInterest"],
    "liabilities_and_equity": ["LiabilitiesAndStockholdersEquity"],
}

SHARE_TAGS = {"diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"]}


def _tag_series(facts: dict, tag: str, unit: str, duration: bool) -> dict[str, float]:
    """{period_end: value} for annual (10-K) facts, keeping the latest-filed value."""
    node = facts.get("facts", {}).get("us-gaap", {}).get(tag)
    if not node or unit not in node["units"]:
        return {}
    best: dict[str, tuple[str, float]] = {}
    for f in node["units"][unit]:
        if f.get("form") not in ANNUAL_FORMS:
            continue
        if duration:
            if "start" not in f:
                continue
            days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days
            if not 350 <= days <= 380:  # 52/53-week fiscal years
                continue
        elif "start" in f:
            continue
        prev = best.get(f["end"])
        if prev is None or f["filed"] > prev[0]:
            best[f["end"]] = (f["filed"], float(f["val"]))
    return {end: v for end, (_, v) in best.items()}


def _merged(facts: dict, tags: list[str], unit: str, duration: bool) -> dict[str, float]:
    out: dict[str, float] = {}
    for tag in tags:
        for end, v in _tag_series(facts, tag, unit, duration).items():
            out.setdefault(end, v)
    return out


def _match_year_end(series: dict[str, float], year_end: str, tolerance_days: int = 7) -> float | None:
    if year_end in series:
        return series[year_end]
    ye = date.fromisoformat(year_end)
    for end, v in series.items():
        if abs((date.fromisoformat(end) - ye).days) <= tolerance_days:
            return v
    return None


def annual_financials(facts: dict, years: int = 5, ltm: bool = True) -> pd.DataFrame:
    """Raw line items (rows) by fiscal-year end date (columns), oldest first.

    With `ltm`, a final "LTM <date>" column is appended when a 10-Q has been filed
    since the last 10-K: flows = FY + current YTD - prior-year YTD, balances = latest 10-Q.
    """
    revenue = _merged(facts, DURATION_TAGS["revenue"], "USD", True)
    if not revenue:
        raise ValueError("No annual revenue found in XBRL facts (non-US-GAAP filer?)")
    year_ends = sorted(revenue)[-years:]

    rows = {}
    for groups, unit, duration in ((DURATION_TAGS, "USD", True), (INSTANT_TAGS, "USD", False),
                                   (SHARE_TAGS, "shares", True)):
        for item, tags in groups.items():
            series = _merged(facts, tags, unit, duration)
            rows[item] = [_match_year_end(series, ye) for ye in year_ends]
    df = pd.DataFrame(rows, index=year_ends).T.astype(float)
    if ltm:
        col = _ltm_column(facts, year_ends[-1])
        if col is not None:
            df[col.name] = col
            df.attrs["latest_yoy_growth"] = col.attrs["ytd_growth"]
    return df


def _ytd(facts: dict, tags: list[str], unit: str, fy_start: date, end: date) -> float | None:
    """10-Q year-to-date value for the period fy_start..end (±7 days), first tag that has it."""
    for tag in tags:
        node = facts.get("facts", {}).get("us-gaap", {}).get(tag)
        if not node or unit not in node["units"]:
            continue
        hits = [f for f in node["units"][unit]
                if f.get("form") == "10-Q" and "start" in f
                and abs((date.fromisoformat(f["start"]) - fy_start).days) <= 7
                and abs((date.fromisoformat(f["end"]) - end).days) <= 7]
        if hits:
            return float(max(hits, key=lambda f: f["filed"])["val"])
    return None


def _ltm_column(facts: dict, last_fye: str) -> pd.Series | None:
    """Last-twelve-months column from the latest 10-Q filed after `last_fye`, if any."""
    fye = date.fromisoformat(last_fye)
    rev_tags = DURATION_TAGS["revenue"]
    ends = [f["end"] for tag in rev_tags
            for f in facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get("USD", [])
            if f.get("form") == "10-Q" and f["end"] > last_fye and "start" in f
            and abs((date.fromisoformat(f["start"]) - fye).days - 1) <= 7]
    if not ends:
        return None
    end = date.fromisoformat(max(ends))
    prior_end = end - timedelta(days=364)
    cur_fy_start = fye + timedelta(days=1)
    prior_fy_start = cur_fy_start - timedelta(days=364)

    values = {}
    for item, tags in DURATION_TAGS.items():
        fy = _match_year_end(_merged(facts, tags, "USD", True), last_fye)
        cur = _ytd(facts, tags, "USD", cur_fy_start, end)
        prior = _ytd(facts, tags, "USD", prior_fy_start, prior_end)
        # If a line isn't tagged in both 10-Qs, fall back to the fiscal-year figure.
        values[item] = fy + cur - prior if None not in (fy, cur, prior) else fy
    for item, tags in INSTANT_TAGS.items():
        values[item] = _match_year_end(_merged_instants_10q(facts, tags), end.isoformat(), tolerance_days=3)
    # Weighted diluted shares: the latest quarter is the most current count available.
    values["diluted_shares"] = (_ytd(facts, SHARE_TAGS["diluted_shares"], "shares", end - timedelta(days=91), end)
                                or _ytd(facts, SHARE_TAGS["diluted_shares"], "shares", cur_fy_start, end))
    rev_cur = _ytd(facts, rev_tags, "USD", cur_fy_start, end)
    rev_prior = _ytd(facts, rev_tags, "USD", prior_fy_start, prior_end)
    if values["revenue"] is None or not rev_cur or not rev_prior:
        return None
    col = pd.Series(values, name=f"LTM {end.isoformat()}", dtype=float)
    col.attrs["ytd_growth"] = rev_cur / rev_prior - 1
    return col


def _merged_instants_10q(facts: dict, tags: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for tag in tags:
        node = facts.get("facts", {}).get("us-gaap", {}).get(tag)
        if not node or "USD" not in node["units"]:
            continue
        best: dict[str, tuple[str, float]] = {}
        for f in node["units"]["USD"]:
            if f.get("form") == "10-Q" and "start" not in f:
                if f["end"] not in best or f["filed"] > best[f["end"]][0]:
                    best[f["end"]] = (f["filed"], float(f["val"]))
        for end, (_, v) in best.items():
            out.setdefault(end, v)
    return out


def shares_outstanding(facts: dict) -> tuple[float | None, str | None]:
    """Latest cover-page share count, summed across share classes."""
    node = facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding")
    if not node:
        return None, None
    entries = node["units"].get("shares", [])
    if not entries:
        return None, None
    latest_end = max(e["end"] for e in entries)
    latest = [e for e in entries if e["end"] == latest_end]
    latest_accn = max(latest, key=lambda e: e["filed"])["accn"]
    return sum(e["val"] for e in latest if e["accn"] == latest_accn), latest_end
