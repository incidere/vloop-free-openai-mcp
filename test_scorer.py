import unittest
from scorer import score_event

class TestScorer(unittest.TestCase):
    def test_low_risk(selfn):
        event = {
            "mint_authority": "5vA...",
            "freeze_authority": "Fz...",
            "holder_balances": [100_000]*10,
            "lp_locked": True,
            "tx_slots": [12345, 12346]
        }
        self.assertLess(score_event(event), 0.2)

    def test_high_risk(selfn):
        event = {
            "mint_authority": None,
            "freeze_authority": None,
            "holder_balances": [900_000, 50_000, 30_000, 10_000, 5_000, 3_000, 1_000, 500, 300, 200],
            "lp_locked": False,
            "tx_slots": [20000, 20000, 20001]
        }
        self.assertGreater(score_event(event), 0.6)

    def test_edge_gini(selfn):
        # Perfect equality should give low gini contribution
        event = {
            "mint_authority": "A1",
            "freeze_authority": "B2",
            "holder_balances": [100]*10,
            "lp_locked": True,
            "tx_slots": [1,2]
        }
        prob = score_event(event)
        self.assertAlmostEqual(prob, 0.0, delta=0.05)

if __name__ == "__main__":
    unittest.main()
