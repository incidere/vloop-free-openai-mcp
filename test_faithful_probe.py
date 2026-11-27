import unittest
from unittest.mock import patch
import faithful_probe


class FaithfulProbeTests(unittest.TestCase):
    def test_inspect_block_counts_transactions_and_token_balances(self):
        block = {
            "blockTime": 123,
            "transactions": [
                {"transaction": {"signatures": ["sig-a"]}, "meta": {
                    "preTokenBalances": [{"mint": "A"}],
                    "postTokenBalances": [{"mint": "A"}, {"mint": "B"}]}},
                {"transaction": {"signatures": ["sig-b"]}, "meta": None},
            ],
        }
        result = faithful_probe.inspect_block(block)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["transaction_count"], 2)
        self.assertEqual(result["transactions_with_metadata"], 1)
        self.assertEqual(result["token_balance_rows"], 3)
        self.assertEqual(result["sample_signature"], "sig-a")

    def test_inspect_null_block_is_not_treated_as_empty_valid_block(self):
        self.assertEqual(faithful_probe.inspect_block(None), {
            "status": "NULL", "transaction_count": 0,
            "metadata_count": 0, "token_balance_rows": 0,
        })

    def test_probe_checks_block_and_sample_transaction(self):
        def fake_rpc(url, method, params, timeout=60):
            if method == "getVersion":
                return {"solana-core": "test"}
            if method == "getFirstAvailableBlock":
                return 10
            if method == "getSlot":
                return 20
            if method == "getBlocks":
                return [15]
            if method == "getBlockTime":
                return 100
            if method == "getBlock":
                return {"blockTime": 100, "transactions": [
                    {"transaction": {"signatures": ["sig"]}, "meta": {}}]}
            if method == "getTransaction":
                return {"slot": 15, "meta": {}}
            raise AssertionError(method)

        with patch.object(faithful_probe, "rpc", side_effect=fake_rpc):
            report = faithful_probe.probe("http://archive.invalid", [15])
        self.assertTrue(report["capabilities"]["getVersion"]["ok"])
        row = report["slots"][0]
        self.assertTrue(row["produced"])
        self.assertEqual(row["block"]["status"], "OK")
        self.assertTrue(row["sample_transaction"]["ok"])

    def test_address_history_failure_is_reported_separately(self):
        def fake_rpc(url, method, params, timeout=60):
            if method == "getVersion":
                return {}
            if method == "getFirstAvailableBlock":
                return 0
            if method == "getSlot":
                return 10
            if method == "getBlocks":
                return []
            if method == "getBlockTime":
                return None
            if method == "getBlock":
                return None
            if method == "getSignaturesForAddress":
                raise RuntimeError("GSFA unavailable")
            raise AssertionError(method)

        with patch.object(faithful_probe, "rpc", side_effect=fake_rpc):
            report = faithful_probe.probe("http://archive.invalid", [5], "address")
        self.assertFalse(report["address_history"]["ok"])
        self.assertEqual(report["slots"][0]["block"]["status"], "NULL")


if __name__ == "__main__":
    unittest.main()
