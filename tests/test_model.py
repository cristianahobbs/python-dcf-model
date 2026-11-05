"""Offline tests: run with `python -m unittest` from the repo root."""
import json
import math
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import manual  # noqa: E402
import model  # noqa: E402

ANT = ROOT / "inputs" / "ANT.json"
ANT_ASSUMPTIONS = ROOT / "inputs" / "ANT_assumptions.json"


def ant_case():
    raw, meta = manual.load_manual(ANT)
    hist = model.standardise(raw)
    a = model.derive_assumptions(hist)
    for k, v in json.loads(ANT_ASSUMPTIONS.read_text(encoding="utf-8")).items():
        if not k.startswith("_"):
            setattr(a, k, v)
    return raw, meta, hist, a


class ManualInputTests(unittest.TestCase):
    def test_ltm_is_fiscal_year_plus_ytd_minus_prior_ytd(self):
        _, _, hist, _ = ant_case()
        # S-1/A: FY2025 4,585 + 9M2026 33,242 - 9M2025 2,545
        self.assertAlmostEqual(hist.income.loc["Revenue", "LTM 2026-09-30"], 35_282)
        self.assertAlmostEqual(hist.income.loc["EBIT", "LTM 2026-09-30"], -8_060 - 1_260 + 5_224)

    def test_convertible_notes_reclassified_to_equity(self):
        _, _, hist, _ = ant_case()
        bs = hist.balance["LTM 2026-09-30"]
        self.assertAlmostEqual(bs["Total liabilities"], 146_683 - 130_502)
        self.assertAlmostEqual(bs["Debt"], 0)
        # Reported deficit + preferred + converted notes
        self.assertAlmostEqual(bs["Shareholders' equity"], -149_873 + 157_510 + 130_502)


class ThreeStatementTests(unittest.TestCase):
    def test_historical_and_projected_balance_sheets_balance(self):
        _, _, hist, a = ant_case()
        proj = model.project(hist, a)
        for df in (hist.balance, proj.balance):
            self.assertLess(df.loc["Balance check"].abs().max(), 1e-6)

    def test_cash_flow_reconciles_to_balance_sheet_cash(self):
        _, _, hist, a = ant_case()
        proj = model.project(hist, a)
        cash = proj.balance.loc["Cash & ST investments"]
        prior = hist.balance.loc["Cash & ST investments"].iloc[-1]
        changes = proj.cashflow.loc[["Cash from operations", "Cash from investing", "Cash from financing"]].sum()
        for col in proj.balance.columns:
            self.assertAlmostEqual(cash[col] - prior, changes[col], places=6)
            prior = cash[col]

    def test_revolver_draws_to_hold_minimum_cash(self):
        _, _, hist, a = ant_case()
        a = replace(a, buyback_pct_net_income=1.5, min_cash_pct_revenue=0.5)
        proj = model.project(hist, a)
        floor = proj.income.loc["Revenue"] * a.min_cash_pct_revenue
        self.assertTrue((proj.balance.loc["Cash & ST investments"] >= floor - 1e-6).all())
        self.assertGreater(proj.balance.loc["Revolver"].max(), 0)


class ValuationTests(unittest.TestCase):
    def test_geometric_fade_starts_and_ends_on_target(self):
        path = model._decay_path(0.90, 0.045, 10, 0.7)
        self.assertAlmostEqual(path[0], 0.90)
        self.assertAlmostEqual(path[-1], 0.045)
        self.assertTrue(all(x > y for x, y in zip(path, path[1:])))

    def test_perpetuity_undefined_when_wacc_not_above_growth(self):
        _, _, hist, a = ant_case()
        proj = model.project(hist, a)
        bridge = model.equity_bridge(hist)
        out = model.dcf(proj, a, bridge, 1000.0, wacc=0.02, terminal_growth=0.03)
        self.assertTrue(math.isnan(out["per_share"]))

    def test_reverse_dcf_reproduces_target_value(self):
        _, _, hist, a = ant_case()
        bridge = model.equity_bridge(hist)
        wacc, target = 0.12, 500.0  # $500bn equity value (shares=1000 -> per_share is $bn)
        shift = model.implied_growth_shift(hist, a, bridge, 1000.0, wacc, target)
        self.assertIsNotNone(shift)
        a2 = replace(a, growth_shift=shift)
        value = model.dcf(model.project(hist, a2), a2, bridge, 1000.0, wacc)["per_share"]
        self.assertAlmostEqual(value, target, places=3)

    def test_value_bridge_ends_at_target_and_forecast_free_check_inverts(self):
        _, _, hist, a = ant_case()
        bridge = model.equity_bridge(hist)
        steps = model.value_bridge(hist, a, bridge, 1000.0, 0.12, 1500.0)
        self.assertEqual(steps[-1]["label"], "Faster revenue growth")
        self.assertAlmostEqual(steps[-1]["value"], 1500.0)
        # Each loosening step should not lower the value
        values = [s["value"] for s in steps[:-1]]
        self.assertTrue(all(b >= a_ - 1e-9 for a_, b in zip(values, values[1:])))
        # EV = FCF (1+g) / (WACC - g) round-trips
        g = model.implied_perpetual_growth(1000.0, 30.0, 0.09)
        self.assertAlmostEqual(30.0 * (1 + g) / (0.09 - g), 1000.0)

    def test_value_falls_as_wacc_rises(self):
        _, _, hist, a = ant_case()
        proj = model.project(hist, a)
        bridge = model.equity_bridge(hist)
        values = [model.dcf(proj, a, bridge, 1000.0, w)["per_share"] for w in (0.10, 0.12, 0.14)]
        self.assertTrue(values[0] > values[1] > values[2])


if __name__ == "__main__":
    unittest.main()
