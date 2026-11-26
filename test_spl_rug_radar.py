#!/usr/bin/env python3
"""
Unit tests for Solana SPL Mint Anomaly & Rug-Pull Radar Scorer (spl_rug_radar.py).
Tests all heuristic dimensions, Gini calculations, calibration, and the <=8KB constraint.
"""

import os
import unittest
from spl_rug_radar import LaunchEvent, score_launch, compute_gini, parse_event


class TestSplRugRadar(unittest.TestCase):
    def setUp(self):
        self.script_path = os.path.join(os.path.dirname(__file__), "spl_rug_radar.py")

    def test_file_size_under_8kb(self):
        size = os.path.getsize(self.script_path)
        self.assertLessEqual(size, 8192, f"spl_rug_radar.py is {size} bytes, exceeds 8,192 byte limit!")

    def test_low_risk_organic_launch(self):
        ev = LaunchEvent(
            mint="SafeSPLToken11111111111111111111111111111111",
            launch_slot=1000,
            event_slot=1008,
            mint_authority_active=False,
            freeze_authority_active=False,
            mutable_metadata=False,
            lp_burn_pct=100.0,
            initial_liq_sol=80.0,
            fdv_sol=350.0,
            holders={
                "11111111111111111111111111111111": 500.0,
                **{f"organic_user_{i}": 15.0 for i in range(25)}
            },
            slot0_buys=2,
            total_early_buys=50,
            unique_funders_in_slot0=6,
            jito_bundle_count=0,
            uniform_buy_cv=0.65,
        )
        report = score_launch(ev)
        self.assertTrue(report["in_window"])
        self.assertEqual(report["block_age"], 8)
        self.assertLess(report["rug_probability"], 0.15)
        self.assertGreater(report["momentum_probability"], 0.80)
        self.assertEqual(report["risk_level"], "LOW")
        self.assertEqual(report["flags"], [])

    def test_critical_risk_unrenounced_mint_and_unlocked_lp(self):
        ev = LaunchEvent(
            mint="RugScamSPL1111111111111111111111111111111111",
            launch_slot=2000,
            event_slot=2002,
            mint_authority_active=True,
            freeze_authority_active=True,
            mutable_metadata=True,
            transfer_fee_bps=500,
            lp_burn_pct=0.0,
            lp_lock_pct=0.0,
            initial_liq_sol=2.0,
            fdv_sol=800.0,
            holders={"scammer_dev": 800.0, "victim1": 10.0},
            slot0_buys=8,
            total_early_buys=10,
            unique_funders_in_slot0=1,
            jito_bundle_count=4,
            uniform_buy_cv=0.02,
        )
        report = score_launch(ev)
        self.assertGreater(report["rug_probability"], 0.95)
        self.assertEqual(report["risk_level"], "CRITICAL")
        self.assertIn("MINT_AUTH_ACTIVE", report["flags"])
        self.assertIn("FREEZE_AUTH_ACTIVE", report["flags"])
        self.assertIn("LP_UNPROTECTED", report["flags"])
        self.assertIn("SLOT0_BUNDLER_COLLUSION", report["flags"])

    def test_holder_gini_and_whale_concentration(self):
        # 1 whale holds 60%, 14 small holders hold remaining
        ev = LaunchEvent(
            mint="WhaleConcentrated11111111111111111111111111",
            launch_slot=3000,
            event_slot=3005,
            mint_authority_active=False,
            freeze_authority_active=False,
            lp_burn_pct=100.0,
            initial_liq_sol=40.0,
            fdv_sol=200.0,
            holders={"whale": 600.0, **{f"u{i}": 20.0 for i in range(14)}},
            total_early_buys=40,
        )
        report = score_launch(ev)
        self.assertGreater(report["components"]["top1_share"], 0.50)
        self.assertGreater(report["components"]["gini"], 0.60)
        self.assertTrue(any("WHALE_TOP1" in f for f in report["flags"]))

    def test_block_window_enforcement(self):
        ev = LaunchEvent(
            mint="LateEvent1111111111111111111111111111111111",
            launch_slot=1000,
            event_slot=1020,  # 20 blocks > 15 limit
            lp_burn_pct=100.0,
            initial_liq_sol=40.0,
            fdv_sol=200.0,
        )
        report = score_launch(ev)
        self.assertFalse(report["in_window"])
        self.assertEqual(report["block_age"], 20)
        self.assertIn("EXCEEDS_15_BLOCK_WINDOW", report["flags"])

    def test_gini_edge_cases(self):
        self.assertEqual(compute_gini([]), (0.0, 0.0, 0.0))
        self.assertEqual(compute_gini([100.0]), (1.0, 1.0, 1.0))
        # Uniform distribution should have Gini near 0
        uniform = [10.0] * 20
        gini, top1, top10 = compute_gini(uniform, 10)
        self.assertAlmostEqual(gini, 0.0, places=2)
        self.assertAlmostEqual(top1, 0.05, places=2)
        self.assertAlmostEqual(top10, 0.50, places=2)

    def test_parse_event_dict_compat(self):
        raw = {
            "mint": "TestMintFromDict",
            "launch_slot": 5000,
            "event_slot": 5003,
            "mint_authority": None,
            "freeze_authority": None,
            "lp_burned": True,
            "holders": [10.0] * 15,
            "initial_liq_sol": 50.0,
            "fdv_sol": 200.0,
            "total_early_buys": 40,
        }
        ev = parse_event(raw)
        self.assertEqual(ev.mint, "TestMintFromDict")
        self.assertFalse(ev.mint_authority_active)
        self.assertEqual(ev.lp_burn_pct, 100.0)
        report = score_launch(ev)
        self.assertTrue(report["in_window"])
        self.assertLess(report["rug_probability"], 0.15)


if __name__ == "__main__":
    unittest.main()
