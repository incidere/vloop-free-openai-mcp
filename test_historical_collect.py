import http.client
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import historical_collect


class HistoricalCollectorTests(unittest.TestCase):
    def test_produced_slots_uses_bounded_inclusive_chunks(self):
        calls = []

        def fake_rpc(url, method, params):
            calls.append((method, params))
            left, right = params
            return list(range(left, right + 1, 2))

        with patch.object(historical_collect, "rpc", side_effect=fake_rpc):
            slots = historical_collect.produced_slots("https://rpc.invalid", 10, 19, 4)

        self.assertEqual([10, 12, 14, 16, 18], slots)
        self.assertEqual(
            [("getBlocks", [10, 13]), ("getBlocks", [14, 17]), ("getBlocks", [18, 19])],
            calls,
        )

    def test_bounded_sampling_is_deterministic_and_within_range(self):
        first = historical_collect.sampled_slot_numbers(100, 1000, 25, 7)
        second = historical_collect.sampled_slot_numbers(100, 1000, 25, 7)
        self.assertEqual(first, second)
        self.assertEqual(25, len(first))
        self.assertEqual(sorted(first), first)
        self.assertTrue(all(100 <= slot <= 1000 for slot in first))

    def test_bounded_sampling_never_exceeds_window_population(self):
        self.assertEqual([10, 11, 12], historical_collect.sampled_slot_numbers(10, 12, 50, 7))
        self.assertEqual([], historical_collect.sampled_slot_numbers(10, 9, 5, 7))

    def test_rpc_retries_incomplete_response_body(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"jsonrpc":"2.0","id":1,"result":"ok"}'

        with patch.object(
            historical_collect.urllib.request,
            "urlopen",
            side_effect=[http.client.IncompleteRead(b"partial", 10), Response()],
        ) as urlopen, patch.object(historical_collect.time, "sleep"):
            result = historical_collect.rpc("https://rpc.invalid", "getHealth", [])
        self.assertEqual("ok", result)
        self.assertEqual(2, urlopen.call_count)

    def test_checkpoint_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "checkpoint.json")
            self.assertEqual(set(), historical_collect.read_checkpoint(path))
            expected = {101, 205, 999}
            historical_collect.write_checkpoint(path, expected)
            self.assertEqual(expected, historical_collect.read_checkpoint(path))

    def test_launch_candidates_have_null_label_until_review(self):
        row = {
            "mint": "ExampleMint",
            "launch_slot": 123,
            "block_time": 1700000000,
            "signature": "ExampleSignature",
            "source": "solana-jsonrpc-getBlock-jsonParsed",
            "label": None,
            "status": "candidate_requires_pool_and_outcome_review",
        }
        self.assertIsNone(row["label"])
        self.assertIn("signature", row)
        self.assertIn("launch_slot", row)


if __name__ == "__main__":
    unittest.main()
