import unittest
from unittest.mock import patch

import historical_reconstruction_pilot as pilot


class HistoricalProgramDiscoveryTests(unittest.TestCase):
    def test_collects_top_level_and_inner_program_ids(self):
        tx = {
            "transaction": {"transaction": {"signatures": ["sig"]},
                "message": {
                    "accountKeys": [{"pubkey": "OWNER"}, {"pubkey": "DEX_A"}],
                    "instructions": [
                        {"programId": "DEX_A", "parsed": {"type": "initializePool"}}
                    ]
                }},
            "meta": {"innerInstructions": [{"index": 0, "instructions": [
                {"programId": "DEX_B", "parsed": {"type": "transfer"}}
            ]}]}
        }
        programs, types = pilot.instruction_programs(tx)
        self.assertEqual(programs, {"DEX_A", "DEX_B"})
        self.assertEqual(types, {"initializePool", "transfer"})

    def test_resolves_compiled_program_id_index(self):
        tx = {
            "transaction": {"message": {
                "accountKeys": ["OWNER", "DEX_COMPILED"],
                "instructions": [{"programIdIndex": 1}]
            }},
            "meta": {"innerInstructions": []}
        }
        programs, types = pilot.instruction_programs(tx)
        self.assertEqual(programs, {"DEX_COMPILED"})
        self.assertEqual(types, set())


    @patch("historical_reconstruction_pilot.rpc")
    def test_mint_init_search_checks_oldest_transactions_first(self, mocked_rpc):
        mint = "MINT_ADDRESS"
        newest_page = [
            {"signature": f"newer-{i}", "slot": 3000 - i, "blockTime": 3000 - i}
            for i in range(999)
        ] + [{"signature": "before-cursor", "slot": 2000, "blockTime": 2000}]
        oldest_page = [
            {"signature": "oldest", "slot": 10, "blockTime": 100},
        ]

        def rpc_side_effect(url, method, params):
            if method == "getSignaturesForAddress":
                if len([x for x in mocked_rpc.call_args_list if x.args[1] == "getSignaturesForAddress"]) == 1:
                    return newest_page, None
                return oldest_page, None
            if method == "getTransaction":
                self.assertEqual(params[0], "oldest")
                return {
                    "transaction": {"message": {"instructions": [{
                        "parsed": {"type": "initializeMint2", "info": {"mint": mint}}
                    }]}}
                }, None
            self.fail("unexpected RPC method: " + method)

        mocked_rpc.side_effect = rpc_side_effect
        result, inspected, error = pilot.find_mint_init("https://rpc.invalid", mint)
        self.assertIsNone(error)
        self.assertEqual(inspected, 1)
        self.assertEqual(result, {"slot": 10, "signature": "oldest", "block_time": 100})
        self.assertEqual(mocked_rpc.call_count, 3)


    @patch("historical_reconstruction_pilot.rpc")
    def test_timestamp_search_finds_mint_without_address_signature_paging(self, mocked_rpc):
        mint = "MINT_BY_TIME"
        launch = {
            "transaction": {"signatures": ["time-launch"], "message": {
                "instructions": [{
                    "programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    "parsed": {"type": "initializeMint2", "info": {"mint": mint}}
                }]
            }},
            "meta": {}
        }

        def rpc_side_effect(url, method, params):
            if method == "getSlot":
                return 10000, None
            if method == "getBlockTime":
                return params[0] // 2, None
            if method == "getBlocks":
                self.assertEqual(params, [4820, 5180])
                return [5000], None
            if method == "getBlock":
                return {"blockTime": 2500, "transactions": [launch]}, None
            self.fail("unexpected RPC method: " + method)

        mocked_rpc.side_effect = rpc_side_effect
        result, inspected, error = pilot.find_mint_init(
            "https://rpc.invalid", mint, created_at=2500
        )
        self.assertIsNone(error)
        self.assertEqual(result, {
            "slot": 5000, "signature": "time-launch", "block_time": 2500
        })
        self.assertEqual(inspected, 1 + len([
            call for call in mocked_rpc.call_args_list
            if call.args[1] == "getBlockTime"
        ]))
        self.assertFalse(any(
            call.args[1] == "getSignaturesForAddress"
            for call in mocked_rpc.call_args_list
        ))

    @patch("historical_reconstruction_pilot.rpc")
    def test_reconstructs_holders_and_authorities_from_historical_window(self, mocked_rpc):
        mint = "MINT_ADDRESS"
        block = {"transactions": [{
            "transaction": {"signatures": ["launch-sig"], "message": {
                "accountKeys": [{"pubkey": "TOKEN_ACCOUNT", "signer": False}],
                "instructions": [{
                    "programId": "TOKEN_PROGRAM",
                    "parsed": {"type": "initializeMint", "info": {
                        "mint": mint, "mintAuthority": "AUTHORITY",
                        "freezeAuthority": None
                    }}
                }]
            }},
            "meta": {"preTokenBalances": [], "postTokenBalances": [{
                "mint": mint, "accountIndex": 0, "owner": "HOLDER",
                "uiTokenAmount": {"amount": "125"}
            }], "innerInstructions": []}
        }]}
        mocked_rpc.return_value = (block, None)
        result = pilot.reconstruct_features(
            "https://rpc.invalid", mint, {"slot": 100},
            {"produced_slots": [100]}, set()
        )
        self.assertEqual(result["features"]["holders"], {"HOLDER": 125})
        self.assertTrue(result["features"]["mint_authority_active"])
        self.assertFalse(result["features"]["freeze_authority_active"])
        self.assertEqual(result["features"]["launch_slot"], 100)
        self.assertEqual(result["features"]["event_slot"], 115)
        self.assertTrue(result["score"]["in_window"])


    @patch("historical_reconstruction_pilot.rpc")
    def test_reconstructs_authority_from_inner_instruction(self, mocked_rpc):
        mint = "MINT_INNER"
        block = {"transactions": [{
            "transaction": {"signatures": ["sig"], "message": {
                "accountKeys": ["TOKEN_PROGRAM", "MINT_ACCOUNT"],
                "instructions": []
            }},
            "meta": {"preTokenBalances": [], "postTokenBalances": [],
                     "innerInstructions": [{"index": 0, "instructions": [{
                         "programId": "TOKEN_PROGRAM",
                         "parsed": {"type": "initializeMint2", "info": {
                             "mint": mint, "mintAuthority": "AUTH", "freezeAuthority": "FREEZE"
                         }}
                     }]}]}
        }]}
        mocked_rpc.return_value = (block, None)
        result = pilot.reconstruct_features(
            "https://rpc.invalid", mint, {"slot": 200},
            {"produced_slots": [200]}, set()
        )
        self.assertTrue(result["features"]["mint_authority_active"])
        self.assertTrue(result["features"]["freeze_authority_active"])

    def test_early_activity_uses_signer_token_balance_deltas(self):
        mint = "MINT_BUY"
        blocks = {300: {"transactions": [{
            "transaction": {"message": {"accountKeys": [
                {"pubkey": "BUYER", "signer": True}
            ]}},
            "meta": {
                "preTokenBalances": [],
                "postTokenBalances": [{
                    "mint": mint, "accountIndex": 0, "owner": "BUYER",
                    "uiTokenAmount": {"amount": "42"}
                }]
            }
        }]}}
        metrics = pilot.slot_metrics_from_blocks(mint, 300, 315, blocks)
        self.assertEqual(metrics["slot0_buys"], 1)
        self.assertEqual(metrics["total_early_buys"], 1)
        self.assertEqual(metrics["unique_funders_in_slot0"], 1)


if __name__ == "__main__":
    unittest.main()
