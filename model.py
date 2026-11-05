"""Three-statement model, DCF valuation and sensitivity tables.

All monetary values are in USD millions; share counts in millions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date

import numpy as np
import pandas as pd

MM = 1e6


def _v(x) -> float:
    return 0.0 if x is None or pd.isna(x) else float(x)


def _first(*xs) -> float | None:
    for x in xs:
        if x is not None and not pd.isna(x):
            return float(x)
    return None


# ---------------------------------------------------------------------------
# Historical statements
# ---------------------------------------------------------------------------
@dataclass
class Historicals:
    income: pd.DataFrame
    balance: pd.DataFrame
    cashflow: pd.DataFrame
    year_ends: list[str]
    warnings: list[str]
    latest_yoy_growth: float | None = None  # YTD vs prior-year YTD revenue growth, when an LTM column exists


def standardise(raw: pd.DataFrame) -> Historicals:
    """Map raw XBRL line items onto a condensed three-statement layout.

    "Other" lines are computed as residuals against reported subtotals, so the
    condensed balance sheet always ties to reported total assets.
    """
    warnings: list[str] = []
    inc, bal, cf = {}, {}, {}
    labels = []
    for end in raw.columns:
        r = {k: (None if pd.isna(v) else v / MM) for k, v in raw[end].items()}
        try:
            date.fromisoformat(end)
            label = f"FY{end[:4]}"
        except ValueError:  # already a label, e.g. "LTM 2026-09-30"
            label = end
        label = label if label not in labels else end
        labels.append(label)

        revenue = r["revenue"]
        cogs = _first(r["cogs"], revenue - r["gross_profit"] if r["gross_profit"] is not None else None)
        if cogs is None:
            warnings.append(f"{end}: no cost of revenue reported; gross margin set to 100%")
            cogs = 0.0
        gross = revenue - cogs
        ebit = _first(r["ebit"], gross - _v(r["rnd"]) - _v(r["sga"]))
        if r["ebit"] is None:
            warnings.append(f"{end}: operating income not tagged; derived as gross profit - R&D - SG&A")
        da = _v(r["da"])
        if r["interest_expense"] is None:
            warnings.append(f"{end}: interest expense not separately tagged (included in other non-operating)")
        pretax = _first(r["pretax_income"], ebit - _v(r["interest_expense"]))
        tax = _v(r["income_tax"])
        net = _first(r["net_income"], pretax - tax)
        dil = _v(r["diluted_shares"])
        inc[label] = {
            "Revenue": revenue,
            "Cost of revenue": -cogs,
            "Gross profit": gross,
            "Operating expenses": -(gross - ebit),
            "EBIT": ebit,
            "Interest expense": -_v(r["interest_expense"]),
            "Other non-operating, net": pretax - ebit + _v(r["interest_expense"]),
            "Pre-tax income": pretax,
            "Income tax": -tax,
            "Net income": net,
            "D&A (memo)": da,
            "EBITDA (memo)": ebit + da,
            "SBC (memo)": _v(r["sbc"]),
            "Diluted shares": dil,
            "Diluted EPS": net / dil if dil else np.nan,
        }

        cash = _v(r["cash"]) + _v(r["st_investments"])
        rec, inv = _v(r["receivables"]), _v(r["inventory"])
        assets = r["total_assets"]
        cur_assets = _first(r["current_assets"], cash + rec + inv)
        if r["current_assets"] is None:
            warnings.append(f"{end}: unclassified balance sheet; other current assets set to 0")
        ppe, lti = _v(r["ppe"]), _v(r["lt_investments"])
        debt_cur = _v(_first(r["ltd_current"], r["debt_current_total"])) + _v(_first(r["st_borrowings"], r["commercial_paper"]))
        debt_nc = _v(r["debt_noncurrent"])
        nci = _v(r["nci"])
        eq_reported = _first(r["equity_parent"], r["equity_total"] - nci if r["equity_total"] is not None else None)
        liab = _first(r["total_liabilities"],
                      assets - r["equity_total"] if r["equity_total"] is not None else None,
                      assets - eq_reported - nci if eq_reported is not None else None)
        cur_liab = _first(r["current_liabilities"], _v(r["payables"]) + debt_cur)
        pay = _v(r["payables"])
        equity = assets - liab - nci
        if eq_reported is not None and abs(equity - eq_reported) > 0.005 * assets:
            warnings.append(f"{end}: equity plug {equity:,.0f} vs reported {eq_reported:,.0f}")
        bal[label] = {
            "Cash & ST investments": cash,
            "Receivables": rec,
            "Inventory": inv,
            "Other current assets": cur_assets - cash - rec - inv,
            "PP&E, net": ppe,
            "LT investments": lti,
            "Other non-current assets": assets - cur_assets - ppe - lti,
            "Total assets": assets,
            "Accounts payable": pay,
            "Other current liabilities": cur_liab - pay - debt_cur,
            "Debt": debt_cur + debt_nc,
            "Revolver": 0.0,
            "Other non-current liabilities": liab - cur_liab - debt_nc,
            "Total liabilities": liab,
            "Non-controlling interest": nci,
            "Shareholders' equity": equity,
            "Total liabilities & equity": liab + nci + equity,
            "Balance check": assets - (liab + nci + equity),
        }

        capex = _v(r["capex"])
        cfo = _v(r["cfo"])
        cf[label] = {
            "Net income": net,
            "D&A": da,
            "Stock-based compensation": _v(r["sbc"]),
            "Working capital & other": cfo - net - da - _v(r["sbc"]),
            "Cash from operations": cfo,
            "Capital expenditure": -capex,
            "Other investing": _v(r["cfi"]) + capex,
            "Cash from investing": _v(r["cfi"]),
            "Dividends": -_v(r["dividends"]),
            "Share repurchases": -_v(r["buybacks"]),
            "Debt & other financing": _v(r["cff"]) + _v(r["dividends"]) + _v(r["buybacks"]),
            "Cash from financing": _v(r["cff"]),
            "Free cash flow (CFO - capex)": cfo - capex,
        }
    return Historicals(pd.DataFrame(inc), pd.DataFrame(bal), pd.DataFrame(cf), list(raw.columns), warnings,
                       raw.attrs.get("latest_yoy_growth"))


# ---------------------------------------------------------------------------
# Assumptions
# ---------------------------------------------------------------------------
@dataclass
class Assumptions:
    profile: str = "stable"  # "stable" (5y, 3-yr averages) or "growth" (10y, latest-year anchors)
    projection_years: int = 5
    # Operating. Paths interpolate linearly from *_start to *_end unless an explicit
    # revenue_growth_path is given; growth_shift / margin_shift move the whole path.
    revenue_growth_start: float = 0.05
    revenue_growth_end: float = 0.03
    revenue_growth_path: list[float] | None = None
    growth_decay: float | None = None  # None = linear fade; e.g. 0.7 = excess growth shrinks 30%/yr
    growth_shift: float = 0.0
    gross_margin: float = 0.40
    ebit_margin_start: float = 0.15
    ebit_margin_end: float = 0.15
    margin_shift: float = 0.0
    tax_rate: float = 0.21
    tax_rate_end: float | None = None
    da_pct_revenue: float = 0.03
    da_pct_revenue_end: float | None = None
    capex_pct_revenue: float = 0.04
    capex_pct_revenue_end: float | None = None  # None = flat; set to fade capex intensity by year N
    sbc_pct_revenue: float = 0.01
    # Working capital (days on revenue / cost of revenue; other items % revenue)
    dso: float = 45.0
    dio: float = 30.0
    dpo: float = 45.0
    other_ca_pct_revenue: float = 0.05
    other_cl_pct_revenue: float = 0.10
    # Financing & capital returns
    payout_ratio: float = 0.30
    buyback_pct_net_income: float = 0.30
    annual_debt_change: float = 0.0
    min_cash_pct_revenue: float = 0.05
    cost_of_debt: float = 0.05
    cash_yield: float = 0.03
    # Valuation
    risk_free_rate: float = 0.0425
    equity_risk_premium: float = 0.05
    beta: float = 1.0
    terminal_growth: float = 0.025
    exit_multiple: float = 12.0
    mid_year_convention: bool = True
    add_back_sbc: bool = False  # street convention; off by default because SBC is a real cost
    target_debt_weight: float | None = None  # None = market-value weights

    def to_dict(self) -> dict:
        return asdict(self)


def annual_fiscal_columns(h: Historicals) -> list[str]:
    return [c for c in h.income.columns if str(c).startswith("FY")]


def derive_assumptions(h: Historicals, market: dict | None = None, lookback: int = 3,
                       profile: str | None = None) -> Assumptions:
    """Default drivers from filed data.

    stable: 3-yr averages, 3-yr CAGR fading over 5 years.
    growth (auto when the 3-yr CAGR > 20%): 10 years, anchored on the latest period, growth fading to
    terminal + 2%, tax ramping from today's effective rate to 23%.
    """
    inc, bal, cf = (df.iloc[:, -lookback:] for df in (h.income, h.balance, h.cashflow))
    rev = inc.loc["Revenue"]
    cogs = -inc.loc["Cost of revenue"]
    avg = lambda s: float(np.nanmean(s))
    last = lambda s: float(s.iloc[-1])
    clamp = lambda x, lo, hi: float(min(max(x, lo), hi))

    # Growth rates use fiscal-year columns only (an LTM column overlaps the last fiscal year).
    fy_rev = h.income.loc["Revenue", annual_fiscal_columns(h)]
    k = min(lookback, len(fy_rev) - 1)
    cagr = (fy_rev.iloc[-1] / fy_rev.iloc[-1 - k]) ** (1 / k) - 1 if k > 0 else 0.04
    profile = profile or ("growth" if cagr > 0.20 else "stable")
    growth = profile == "growth"
    tg = 0.025
    if growth:
        latest = _latest_growth(h)
        g0 = clamp(latest if latest is not None else cagr, -0.10, 1.50)
    else:
        g0 = clamp(cagr, -0.05, 0.25)
    pick = last if growth else avg

    pretax, tax = inc.loc["Pre-tax income"], -inc.loc["Income tax"]
    tax_rate = clamp(tax.sum() / pretax.sum(), 0.10, 0.30) if pretax.sum() > 0 else 0.21
    if growth:  # NOL-shielded growers: today's cash tax rate, normalising to statutory
        tax_rate = clamp(last(tax) / last(pretax), 0.0, 0.30) if last(pretax) > 0 else 0.0

    # Cost of debt: latest year that reports interest expense / average debt that year.
    rf = 0.0425
    kd = rf + 0.015
    debt, interest = h.balance.loc["Debt"], -h.income.loc["Interest expense"]
    for i in range(len(debt) - 1, 0, -1):
        avg_debt = (debt.iloc[i] + debt.iloc[i - 1]) / 2
        if interest.iloc[i] > 0 and avg_debt > 0:
            kd = clamp(interest.iloc[i] / avg_debt, 0.02, 0.12)
            break

    ni = inc.loc["Net income"].clip(lower=1e-9)
    margin = pick(inc.loc["EBIT"] / rev)

    a = Assumptions(
        profile=profile,
        projection_years=10 if growth else 5,
        revenue_growth_start=g0,
        revenue_growth_end=tg + 0.02 if growth else (g0 + tg) / 2,
        growth_decay=0.7 if growth else None,
        gross_margin=pick(inc.loc["Gross profit"] / rev),
        ebit_margin_start=margin,
        ebit_margin_end=margin,
        tax_rate=tax_rate,
        tax_rate_end=0.23 if growth else None,
        da_pct_revenue=pick(inc.loc["D&A (memo)"] / rev),
        capex_pct_revenue=pick(-cf.loc["Capital expenditure"] / rev),
        sbc_pct_revenue=pick(inc.loc["SBC (memo)"] / rev),
        # Working capital is anchored to the latest year so year 1 has no artificial NWC swing.
        dso=last(bal.loc["Receivables"] / rev * 365),
        dio=last(bal.loc["Inventory"] / cogs * 365) if cogs.iloc[-1] else 0.0,
        dpo=last(bal.loc["Accounts payable"] / cogs * 365) if cogs.iloc[-1] else 0.0,
        other_ca_pct_revenue=last(bal.loc["Other current assets"] / rev),
        other_cl_pct_revenue=last(bal.loc["Other current liabilities"] / rev),
        payout_ratio=clamp(avg(-cf.loc["Dividends"] / ni), 0.0, 1.0),
        buyback_pct_net_income=clamp(avg(-cf.loc["Share repurchases"] / ni), 0.0, 1.5),
        min_cash_pct_revenue=0.05,
        cost_of_debt=kd,
        cash_yield=max(rf - 0.01, 0.0),
        risk_free_rate=rf,
        terminal_growth=tg,
    )
    if market and market.get("beta"):
        a.beta = round(market["beta"], 2)
    if growth:
        a.exit_multiple = 20.0  # a mature-company multiple at year 10, not today's
    return a


def _latest_growth(h: Historicals) -> float | None:
    """Year-over-year growth of the latest period: LTM vs the prior LTM when available, else last FY."""
    if h.latest_yoy_growth is not None:
        return h.latest_yoy_growth
    fy = annual_fiscal_columns(h)
    rev = h.income.loc["Revenue"]
    if len(fy) >= 2:
        return rev[fy[-1]] / rev[fy[-2]] - 1
    return None


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------
@dataclass
class Projection:
    income: pd.DataFrame
    balance: pd.DataFrame
    cashflow: pd.DataFrame
    fcff: pd.DataFrame


def _path(start: float, end: float, n: int) -> np.ndarray:
    return np.linspace(start, end, n) if n > 1 else np.array([start])


def _decay_path(start: float, end: float, n: int, decay: float) -> np.ndarray:
    """Excess growth over `end` shrinks by (1 - decay) a year, reaching `end` exactly in year n."""
    if n == 1:
        return np.array([start])
    w = (decay ** np.arange(n) - decay ** (n - 1)) / (1 - decay ** (n - 1))
    return end + (start - end) * w


def projection_labels(h: Historicals, n: int) -> list[str]:
    """FY2027E... after a fiscal-year base; Sep-27E... (12-month periods) after an LTM base."""
    base_label = str(h.income.columns[-1])
    if base_label.startswith("FY"):
        return [f"FY{int(base_label[2:6]) + i}E" for i in range(1, n + 1)]
    end = date.fromisoformat(h.year_ends[-1][-10:])
    return [f"{end:%b}-{(end.year + i) % 100:02d}E" for i in range(1, n + 1)]


def project(h: Historicals, a: Assumptions) -> Projection:
    """Fully linked projection: IS -> CF -> BS, with a cash sweep / revolver.

    Interest is charged on opening balances, which avoids a circular reference.
    """
    base_label = h.income.columns[-1]
    if a.revenue_growth_path:
        growth = np.asarray(a.revenue_growth_path, dtype=float)
        n = len(growth)
    else:
        n = a.projection_years
        if a.growth_decay is None:
            growth = _path(a.revenue_growth_start, a.revenue_growth_end, n)
        else:
            growth = _decay_path(a.revenue_growth_start, a.revenue_growth_end, n, a.growth_decay)
    growth = growth + a.growth_shift
    labels = projection_labels(h, n)
    margin = _path(a.ebit_margin_start, a.ebit_margin_end, n) + a.margin_shift
    end_or = lambda end, start: start if end is None else end
    capex_pct = _path(a.capex_pct_revenue, end_or(a.capex_pct_revenue_end, a.capex_pct_revenue), n)
    da_pct = _path(a.da_pct_revenue, end_or(a.da_pct_revenue_end, a.da_pct_revenue), n)
    tax_rate = _path(a.tax_rate, end_or(a.tax_rate_end, a.tax_rate), n)

    b = h.balance[base_label]
    prev = {
        "rev": h.income.loc["Revenue", base_label],
        "cash": b["Cash & ST investments"], "ppe": b["PP&E, net"], "debt": b["Debt"],
        "revolver": 0.0, "equity": b["Shareholders' equity"],
        "nwc": b["Receivables"] + b["Inventory"] + b["Other current assets"]
               - b["Accounts payable"] - b["Other current liabilities"],
    }
    lti, onca = b["LT investments"], b["Other non-current assets"]
    oncl, nci = b["Other non-current liabilities"], b["Non-controlling interest"]

    inc, bal, cf, fcff = {}, {}, {}, {}
    for i, lab in enumerate(labels):
        rev = prev["rev"] * (1 + growth[i])
        cogs = rev * (1 - a.gross_margin)
        gross = rev - cogs
        ebit = rev * margin[i]
        da = rev * da_pct[i]
        sbc = rev * a.sbc_pct_revenue
        int_exp = a.cost_of_debt * (prev["debt"] + prev["revolver"])
        int_inc = a.cash_yield * prev["cash"]
        pretax = ebit - int_exp + int_inc
        tax = max(pretax, 0.0) * tax_rate[i]
        net = pretax - tax

        rec = rev * a.dso / 365
        inv = cogs * a.dio / 365
        pay = cogs * a.dpo / 365
        oca = rev * a.other_ca_pct_revenue
        ocl = rev * a.other_cl_pct_revenue
        nwc = rec + inv + oca - pay - ocl
        d_nwc = nwc - prev["nwc"]
        capex = rev * capex_pct[i]
        ppe = prev["ppe"] + capex - da

        cfo = net + da + sbc - d_nwc
        dividends = max(net, 0.0) * a.payout_ratio
        buybacks = max(net, 0.0) * a.buyback_pct_net_income
        d_debt = prev["debt"] * a.annual_debt_change
        debt = prev["debt"] + d_debt

        cash_pre = prev["cash"] + cfo - capex - dividends - buybacks + d_debt
        min_cash = rev * a.min_cash_pct_revenue
        if cash_pre < min_cash:
            d_rev = min_cash - cash_pre                        # draw
        else:
            d_rev = -min(prev["revolver"], cash_pre - min_cash)  # sweep excess to repay
        revolver = prev["revolver"] + d_rev
        cash = cash_pre + d_rev
        equity = prev["equity"] + net + sbc - dividends - buybacks

        assets = cash + rec + inv + oca + ppe + lti + onca
        liab = pay + ocl + debt + revolver + oncl

        inc[lab] = {
            "Revenue": rev, "Cost of revenue": -cogs, "Gross profit": gross,
            "Operating expenses": -(gross - ebit), "EBIT": ebit,
            "Interest expense": -int_exp, "Other non-operating, net": int_inc,
            "Pre-tax income": pretax, "Income tax": -tax, "Net income": net,
            "D&A (memo)": da, "EBITDA (memo)": ebit + da, "SBC (memo)": sbc,
            "Revenue growth": growth[i], "EBIT margin": margin[i],
        }
        bal[lab] = {
            "Cash & ST investments": cash, "Receivables": rec, "Inventory": inv,
            "Other current assets": oca, "PP&E, net": ppe, "LT investments": lti,
            "Other non-current assets": onca, "Total assets": assets,
            "Accounts payable": pay, "Other current liabilities": ocl, "Debt": debt,
            "Revolver": revolver, "Other non-current liabilities": oncl,
            "Total liabilities": liab, "Non-controlling interest": nci,
            "Shareholders' equity": equity, "Total liabilities & equity": liab + nci + equity,
            "Balance check": assets - (liab + nci + equity),
        }
        cf[lab] = {
            "Net income": net, "D&A": da, "Stock-based compensation": sbc,
            "Working capital & other": -d_nwc, "Cash from operations": cfo,
            "Capital expenditure": -capex, "Other investing": 0.0, "Cash from investing": -capex,
            "Dividends": -dividends, "Share repurchases": -buybacks,
            "Debt & other financing": d_debt + d_rev,
            "Cash from financing": -dividends - buybacks + d_debt + d_rev,
            "Free cash flow (CFO - capex)": cfo - capex,
            "Net change in cash": cash - prev["cash"],
        }
        nopat = ebit - max(ebit, 0.0) * tax_rate[i]  # no tax credit on operating losses
        sbc_back = sbc if a.add_back_sbc else 0.0
        fcff[lab] = {
            "EBIT": ebit, "Taxes on EBIT": nopat - ebit, "NOPAT": nopat,
            "D&A": da, "SBC added back": sbc_back, "Capital expenditure": -capex, "Change in NWC": -d_nwc,
            "Unlevered FCF": nopat + da + sbc_back - capex - d_nwc,
            "EBITDA": ebit + da,
        }
        prev = {"rev": rev, "cash": cash, "ppe": ppe, "debt": debt, "revolver": revolver,
                "equity": equity, "nwc": nwc}

    return Projection(pd.DataFrame(inc), pd.DataFrame(bal), pd.DataFrame(cf), pd.DataFrame(fcff))


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------
def compute_wacc(a: Assumptions, market_cap: float | None, debt: float) -> dict:
    ke = a.risk_free_rate + a.beta * a.equity_risk_premium
    kd_at = a.cost_of_debt * (1 - a.tax_rate)
    if a.target_debt_weight is not None:
        wd = a.target_debt_weight
    elif market_cap:
        wd = debt / (debt + market_cap)
    else:
        wd = 0.10
    return {"cost_of_equity": ke, "after_tax_cost_of_debt": kd_at,
            "equity_weight": 1 - wd, "debt_weight": wd, "wacc": (1 - wd) * ke + wd * kd_at}


def equity_bridge(h: Historicals) -> dict:
    b = h.balance.iloc[:, -1]
    return {"cash": b["Cash & ST investments"], "lt_investments": b["LT investments"],
            "debt": b["Debt"], "nci": b["Non-controlling interest"]}


def dcf(p: Projection, a: Assumptions, bridge: dict, shares: float, wacc: float,
        terminal_growth: float | None = None, exit_multiple: float | None = None) -> dict:
    """Enterprise and per-share value by perpetuity growth (default) or exit multiple."""
    g = a.terminal_growth if terminal_growth is None else terminal_growth
    fcf = p.fcff.loc["Unlevered FCF"].to_numpy()
    n = len(fcf)
    t = np.arange(1, n + 1) - (0.5 if a.mid_year_convention else 0.0)
    disc = (1 + wacc) ** -t
    pv_fcf = float((fcf * disc).sum())

    if exit_multiple is not None:
        tv = p.fcff.loc["EBITDA"].iloc[-1] * exit_multiple
        tv_t = n  # a multiple values the business at the end of year N
    else:
        if wacc <= g:
            return {"enterprise_value": np.nan, "equity_value": np.nan, "per_share": np.nan}
        tv = fcf[-1] * (1 + g) / (wacc - g)
        tv_t = n - (0.5 if a.mid_year_convention else 0.0)
    pv_tv = tv / (1 + wacc) ** tv_t
    ev = pv_fcf + pv_tv
    eq = ev + bridge["cash"] + bridge["lt_investments"] - bridge["debt"] - bridge["nci"]
    ebitda_n = p.fcff.loc["EBITDA"].iloc[-1]
    return {
        "pv_fcf": pv_fcf, "terminal_value": tv, "pv_terminal_value": pv_tv,
        "enterprise_value": ev, "equity_value": eq, "per_share": eq / shares,
        "tv_share_of_ev": pv_tv / ev, "discount_factors": disc,
        "implied_exit_multiple": tv / ebitda_n,
        "implied_terminal_growth": (tv * wacc - fcf[-1]) / (tv + fcf[-1]),
    }


# ---------------------------------------------------------------------------
# Sensitivities
# ---------------------------------------------------------------------------
def _steps(center: float, step: float, k: int = 2) -> list[float]:
    return [round(center + step * i, 6) for i in range(-k, k + 1)]


def sensitivity_wacc_growth(p, a, bridge, shares, wacc, step_w=0.005, step_g=0.005) -> pd.DataFrame:
    ws, gs = _steps(wacc, step_w), _steps(a.terminal_growth, step_g)
    data = [[dcf(p, a, bridge, shares, w, terminal_growth=g)["per_share"] for g in gs] for w in ws]
    return pd.DataFrame(data, index=pd.Index([f"{w:.2%}" for w in ws], name="WACC"),
                        columns=pd.Index([f"{g:.2%}" for g in gs], name="Terminal growth"))


def sensitivity_wacc_multiple(p, a, bridge, shares, wacc, step_w=0.005, step_m=2.0) -> pd.DataFrame:
    ws, ms = _steps(wacc, step_w), [m for m in _steps(a.exit_multiple, step_m) if m > 0]
    data = [[dcf(p, a, bridge, shares, w, exit_multiple=m)["per_share"] for m in ms] for w in ws]
    return pd.DataFrame(data, index=pd.Index([f"{w:.2%}" for w in ws], name="WACC"),
                        columns=pd.Index([f"{m:.1f}x" for m in ms], name="Exit EV/EBITDA"))


def _value_at(h, a, bridge, shares, wacc, **shifts) -> float:
    a2 = replace(a, **shifts)
    return dcf(project(h, a2), a2, bridge, shares, wacc)["per_share"]


def sensitivity_growth_margin(h, a, bridge, shares, wacc, step_g=0.02, step_m=0.02) -> pd.DataFrame:
    """Re-runs the whole projection with the revenue-growth and EBIT-margin paths shifted in parallel."""
    gshift, mshift = _steps(0.0, step_g), _steps(0.0, step_m)
    base = project(h, a)
    g = base.income.loc["Revenue growth"]
    m_end = base.income.loc["EBIT margin"].iloc[-1]
    data = [[_value_at(h, a, bridge, shares, wacc, growth_shift=a.growth_shift + dg,
                       margin_shift=a.margin_shift + dm) for dm in mshift] for dg in gshift]
    return pd.DataFrame(
        data,
        index=pd.Index([f"{g.iloc[0] + d:.1%} -> {g.iloc[-1] + d:.1%}" for d in gshift],
                       name="Revenue growth (yr1 -> yrN)"),
        columns=pd.Index([f"{m_end + d:.1%}" for d in mshift], name="Terminal EBIT margin"))


def value_bridge(h, a, bridge, shares, wacc, price) -> list[dict]:
    """Walk from the base-case value to the market price by loosening assumptions one at a time
    (each step is cumulative), then solve for the extra revenue growth that closes the remaining gap."""
    val = lambda a2, w: dcf(project(h, a2), a2, bridge, shares, w)["per_share"]
    steps = [{"label": "Base case", "value": val(a, wacc), "detail": f"WACC {wacc:.1%}, g {a.terminal_growth:.1%}"}]
    cur, w = a, wacc

    def add(label, detail, a2, w2):
        nonlocal cur, w
        cur, w = a2, w2
        steps.append({"label": label, "value": val(cur, w), "detail": detail})

    latest_margin = float(h.income.loc["EBIT"].iloc[-1] / h.income.loc["Revenue"].iloc[-1])
    if latest_margin > cur.ebit_margin_end + 0.001:  # only when it is the more generous assumption
        add("Latest-year margin", f"{cur.ebit_margin_end:.1%} -> {latest_margin:.1%}",
            replace(cur, ebit_margin_start=latest_margin, ebit_margin_end=latest_margin), w)
    add("Add back SBC", "street convention", replace(cur, add_back_sbc=True), w)
    add("WACC -1pt", f"{w:.1%} -> {w - 0.01:.1%}", cur, w - 0.01)
    add("Terminal growth +0.5pt", f"{cur.terminal_growth:.1%} -> {cur.terminal_growth + 0.005:.1%}",
        replace(cur, terminal_growth=cur.terminal_growth + 0.005), w)

    shift = implied_growth_shift(h, cur, bridge, shares, w, price)
    if shift is not None:
        base_g = project(h, cur).income.loc["Revenue growth"].mean()
        new_g = project(h, replace(cur, growth_shift=cur.growth_shift + shift)).income.loc["Revenue growth"].mean()
        steps.append({"label": "Faster revenue growth", "value": price,
                      "detail": f"avg {base_g:.1%} -> {new_g:.1%}/yr", "growth_shift": shift,
                      "avg_growth_before": float(base_g), "avg_growth_after": float(new_g)})
    return steps


def implied_perpetual_growth(enterprise_value: float, fcf: float, wacc: float) -> float:
    """Growth rate g such that EV = FCF x (1 + g) / (WACC - g): the forecast-free sanity check."""
    return (wacc * enterprise_value - fcf) / (enterprise_value + fcf)


def implied_growth_shift(h, a, bridge, shares, wacc, target: float, lo=-0.5, hi=1.5) -> float | None:
    """Reverse DCF: parallel shift of the revenue-growth path at which the
    perpetuity-method value equals `target` (e.g. the share price). None if no root in [lo, hi]."""
    f = lambda s: _value_at(h, a, bridge, shares, wacc, growth_shift=s) - target
    flo, fhi = f(lo), f(hi)
    if np.isnan(flo) or np.isnan(fhi) or flo * fhi > 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) < 1e-6:
            break
        lo, hi, flo = (mid, hi, fm) if fm * flo > 0 else (lo, mid, flo)
    return (lo + hi) / 2
