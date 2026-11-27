import unittest
from spl_rug_radar import LaunchEvent, compute_gini, parse_event, score_launch

class Tests(unittest.TestCase):
    def safe(self, **kw):
        d = dict(mint="M", launch_slot=100, event_slot=108,
                 holders={f"h{i}": 10 for i in range(20)},
                 lp_burn_pct=100, lp_burn_verified=True,
                 initial_liq_sol=60, fdv_sol=200, total_early_buys=40,
                 slot0_buys=2, unique_funders_in_slot0=2, buy_amount_cv=.8)
        d.update(kw)
        return LaunchEvent(**d)

    def test_gini_full_distribution(self):
        self.assertAlmostEqual(compute_gini([10] * 20), 0)
        self.assertAlmostEqual(compute_gini([100] + [1] * 99), .49, places=2)
        self.assertEqual(compute_gini([]), 0)

    def test_whale_concentration_and_lp_exclusion(self):
        e = self.safe(holders={"whale": 600, **{f"u{i}": 20 for i in range(14)},
                               "lp": 1000}, lp_vaults=["lp"])
        r = score_launch(e)
        self.assertGreater(r["components"]["top1_share"], .5)
        self.assertGreater(r["components"]["gini"], .6)

    def test_unverified_burn_is_not_counted(self):
        e = self.safe(lp_burn_pct=100, lp_burn_verified=False)
        r = score_launch(e)
        self.assertIn("LP_PROTECTION_UNVERIFIED", r["flags"])
        self.assertFalse(r["components"]["lp_protection_verified"])

    def test_verified_lock_counts(self):
        e = self.safe(lp_burn_pct=0, lp_burn_verified=False,
                      lp_lock_pct=100, lp_lock_days=90, lp_lock_verified=True)
        self.assertTrue(score_launch(e)["components"]["lp_protection_verified"])

    def test_window_boundary_and_rejection(self):
        self.assertTrue(score_launch(self.safe(event_slot=115))["in_window"])
        r = score_launch(self.safe(event_slot=116))
        self.assertFalse(r["in_window"])
        self.assertIsNone(r["rug_probability"])
        with self.assertRaises(ValueError):
            score_launch(self.safe(event_slot=99))

    def test_parse_event_does_not_trust_claimed_burn(self):
        e = parse_event({"mint":"M", "launch_slot":1, "event_slot":2,
                         "lp_burned":True, "lp_burn_pct":100})
        self.assertFalse(e.lp_burn_verified)

    def test_bad_input_rejected(self):
        with self.assertRaises(ValueError):
            score_launch(self.safe(lp_burn_pct=101))
        with self.assertRaises(ValueError):
            score_launch(self.safe(fdv_sol=float("nan")))

    def test_missing_liquidity_is_not_mislabeled_thin(self):
        r=score_launch(self.safe(initial_liq_sol=0,fdv_sol=0))
        self.assertIn("LIQUIDITY_DEPTH_UNAVAILABLE",r["flags"])
        self.assertNotIn("THIN_LIQUIDITY",r["flags"])

    def test_output_probabilities_bounded_and_labeled(self):
        r = score_launch(self.safe())
        self.assertTrue(0 <= r["rug_probability"] <= 1)
        self.assertTrue(0 <= r["momentum_probability"] <= 1)
        self.assertIn("not trained", r["calibration"])

if __name__ == "__main__": unittest.main()
