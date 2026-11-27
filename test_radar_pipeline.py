import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import backtest, solana_ingest

class BacktestTests(unittest.TestCase):
    def test_pav_is_monotone(self):
        k=backtest.pav([(0.1,1),(0.2,0),(0.3,1),(0.4,0)])
        ys=[v for _,v in k]
        self.assertEqual(ys,sorted(ys))
    def test_apply_steps(self):
        self.assertEqual(backtest.apply(.15,[[.2,.3],[1,.8]]),.3)
        self.assertEqual(backtest.apply(.9,[[.2,.3],[1,.8]]),.8)
    def test_ranking_metrics_perfect_order_and_ties(self):
        m=backtest.ranking_metrics([(0.1,0),(0.2,0),(0.8,1),(0.9,1)])
        self.assertEqual(m,{"roc_auc":1.0,"average_precision":1.0})
        tied=backtest.ranking_metrics([(0.5,0),(0.5,1)])
        self.assertEqual(tied,{"roc_auc":0.5,"average_precision":0.5})

    def test_ranking_metrics_returns_none_for_single_class(self):
        self.assertEqual(backtest.ranking_metrics([(0.1,0),(0.8,0)]),
          {"roc_auc":None,"average_precision":None})

    def test_chronological_split_keeps_same_slot_together(self):
        rows=[(10,.1,0),(10,.2,1),(11,.3,0),(12,.8,1),(12,.9,0)]
        train,test=backtest.chronological_split(rows,.6)
        self.assertTrue(max(x[0] for x in train)<min(x[0] for x in test))
        self.assertEqual(len(train)+len(test),len(rows))

    def test_metrics_counts_and_bounds(self):
        m=backtest.metrics([(.9,1),(.8,0),(.1,0),(.2,1)])
        self.assertEqual((m["tp"],m["fp"],m["tn"],m["fn"]),(1,1,1,1))
        self.assertTrue(0<=m["brier"]<=1 and 0<=m["ece"]<=1)

class IngestTests(unittest.TestCase):
    def test_token2022_features_read_transfer_fee_and_mutable_metadata(self):
        info={"mintAuthority":"Authority","extensions":[
          {"extension":"transferFeeConfig","state":{
            "olderTransferFee":{"transferFeeBasisPoints":125},
            "newerTransferFee":{"transferFeeBasisPoints":250}}},
          {"extension":"tokenMetadata","state":{"isMutable":True}}]}
        self.assertEqual(solana_ingest.mint_risk_features(info),
          {"metadata_mutable":True,"transfer_fee_bps":250})

    def test_provider_string_false_is_not_mutable_metadata(self):
        self.assertFalse(solana_ingest.explicit_bool("false",True))
        self.assertFalse(solana_ingest.mint_risk_features({"mutable":"false"})["metadata_mutable"])
        self.assertTrue(solana_ingest.mint_risk_features({"mutable":"true"})["metadata_mutable"])

    def test_transfer_fee_outside_basis_point_range_is_ignored(self):
        info={"extensions":[{"extension":"transferFeeConfig","state":{
          "newerTransferFee":{"transferFeeBasisPoints":10001}}}]}
        self.assertEqual(solana_ingest.mint_risk_features(info)["transfer_fee_bps"],0)

    def test_token2022_unknown_extensions_do_not_invent_risk(self):
        self.assertEqual(solana_ingest.mint_risk_features({"extensions":[
          {"extension":"nonTransferExtension","state":{"enabled":True}}]}),
          {"metadata_mutable":False,"transfer_fee_bps":0})

    def test_mint_features_support_flattened_rpc_shape(self):
        info={"mutable":False,"newerTransferFee":{"transferFeeBasisPoints":"75"}}
        self.assertEqual(solana_ingest.mint_risk_features(info),
          {"metadata_mutable":False,"transfer_fee_bps":75})

    def test_mint_init_detection(self):
        tx={"transaction":{"message":{"instructions":[
          {"parsed":{"type":"initializeMint","info":{"mint":"Mint111111111111111111111111111111111"}}}]}}}
        self.assertEqual(solana_ingest.launch_mints(tx),["Mint111111111111111111111111111111111"])
    def test_lp_burn_detection_includes_inner_instructions(self):
        tx={"transaction":{"message":{"instructions":[
          {"parsed":{"type":"transfer","info":{"mint":"OTHER"}}}]}},
          "meta":{"innerInstructions":[{"index":0,"instructions":[
            {"parsed":{"type":"burnChecked","info":{"mint":"LP111111111111111111111111111111111","amount":"10"}}},
            {"parsed":{"type":"burn","info":{"mint":"LP222222222222222222222222222222222","amount":"5"}}}]}]}}
        self.assertEqual(solana_ingest.lp_burn_mints(tx),{
          "LP111111111111111111111111111111111","LP222222222222222222222222222222222"})
    def test_pool_parser_uses_configured_program(self):
        tx={"transaction":{"message":{"instructions":[
          {"programId":"DEX","parsed":{"type":"initializePool","info":{
            "tokenAMint":"Mint111111111111111111111111111111111","tokenBMint":"Quote11111111111111111111111111111111","lpMint":"LP111111111111111111111111111111111",
            "tokenAVault":"Vault11111111111111111111111111111111"}}}]}}}
        p=solana_ingest.pool_info(tx,{"DEX"})
        self.assertEqual(p["lp_mint"],"LP111111111111111111111111111111111")
        self.assertIn("Mint111111111111111111111111111111111",p["mints"])
        self.assertEqual(p["vaults"],["Vault11111111111111111111111111111111"])
    def test_pool_parser_excludes_lp_share_mint(self):
        tx={"transaction":{"message":{"instructions":[
          {"programId":"DEX","parsed":{"type":"initializePool","info":{
            "tokenAMint":"ASSET11111111111111111111111111111111",
            "tokenBMint":"QUOTE1111111111111111111111111111111",
            "lpMint":"SHARE1111111111111111111111111111111"}}}]}}}
        p=solana_ingest.pool_info(tx,{"DEX"})
        self.assertEqual(p["mints"],{
          "ASSET11111111111111111111111111111111",
          "QUOTE1111111111111111111111111111111"})
        self.assertNotIn(p["lp_mint"],p["mints"])

    def test_pool_parser_does_not_guess_assets_from_unrelated_balances(self):
        tx={"transaction":{"message":{"instructions":[
          {"programId":"DEX","parsed":{"type":"initializePool","info":{}}}]}},
          "meta":{"postTokenBalances":[
            {"mint":"UNRELATED1111111111111111111111111111"}]}}
        self.assertIsNone(solana_ingest.pool_info(tx,{"DEX"}))

    def test_raydium_v4_initialize2_decoder(self):
        a=[f"Account{i}" for i in range(21)]
        tx={"transaction":{"message":{"instructions":[
          {"programId":solana_ingest.RAYDIUM_V4,"data":"2","accounts":a}]}}}
        p=solana_ingest.pool_info(tx,{solana_ingest.RAYDIUM_V4})
        self.assertEqual(p["lp_mint"],a[7])
        self.assertEqual(p["mints"],{a[8],a[9]})
        self.assertEqual(p["vaults"],[a[10],a[11]])
    def test_pumpswap_create_pool_decoder(self):
        a=[f"Account{i}" for i in range(18)]
        tx={"transaction":{"message":{"instructions":[{
            "programId":solana_ingest.PUMP_AMM,
            "data":"g4xRkfehbgF",
            "accounts":a
        }]}}}
        p=solana_ingest.pool_info(tx,{solana_ingest.PUMP_AMM})
        self.assertEqual(p["mints"],{a[3],a[4]})
        self.assertEqual(p["lp_mint"],a[5])
        self.assertEqual(p["vaults"],[a[9],a[10]])

    def test_pool_parser_ignores_unconfigured_program(self):
        tx={"transaction":{"message":{"instructions":[
          {"programId":"OTHER","parsed":{"type":"initializePool","info":{"tokenMint":"Mint111111111111111111111111111111111"}}}]}}}
        self.assertIsNone(solana_ingest.pool_info(tx,{"DEX"}))
    def test_locker_event_lp_mint_extraction(self):
        tx={"transaction":{"message":{"instructions":[
          {"programId":"LOCK","parsed":{"type":"lock","info":{"lpMint":"LP-A"}}},
          {"programId":"OTHER","parsed":{"type":"transfer","info":{"mint":"NOT-LP"}}},
          {"programId":"LOCK","parsed":{"type":"unlock","info":{"mint":"LP-B"}}}]}}}
        self.assertEqual(solana_ingest.lock_event_lp_mints(tx),{"LP-A"})

    def test_lp_burn_and_lock_require_onchain_proof(self):
        pool={"meta":{"err":None},"transaction":{"message":{"instructions":[
          {"parsed":{"type":"mintTo","info":{"mint":"LP","amount":"1000"}}},
          {"parsed":{"type":"burnChecked","info":{"mint":"LP","amount":"500"}}}]}}}
        lock={"transaction":{"message":{"instructions":[
          {"programId":"LOCK","parsed":{"type":"lock","info":{"lpMint":"LP","amount":"250","tokenAccount":"LOCKVAULT","unlockTime":1000+90*86400}}}]}}}
        def fake_rpc(url,method,params):
            if method=="getBlockTime":return 1000
            if method=="getAccountInfo":return {"value":{"data":{"parsed":{"info":{"mint":"LP","tokenAmount":{"amount":"250"}}}}}}
            raise AssertionError(method)
        with patch.object(solana_ingest,"rpc",side_effect=fake_rpc):
            burn,bv,locked,days,lv=solana_ingest.lp_proof("rpc",pool,"LP",{"LOCK"},5,lock)
        self.assertEqual(burn,50)
        self.assertTrue(bv)
        self.assertEqual((locked,days,lv),(25,90,True))

    def test_unlock_instruction_cannot_prove_lp_lock(self):
        pool={"transaction":{"message":{"instructions":[
          {"parsed":{"type":"mintTo","info":{"mint":"LP","amount":"1000"}}}]}}}
        unlock={"transaction":{"message":{"instructions":[
          {"programId":"LOCK","parsed":{"type":"unlock","info":{"lpMint":"LP","amount":"250","tokenAccount":"VAULT","unlockTime":1000+90*86400}}}]}}}
        with patch.object(solana_ingest,"rpc",return_value=1000):
            _,_,locked,days,verified=solana_ingest.lp_proof("rpc",pool,"LP",{"LOCK"},5,unlock)
        self.assertEqual((locked,days,verified),(0.0,0,False))

    def test_lp_lock_requires_matching_vault_balance(self):
        pool={"transaction":{"message":{"instructions":[
          {"parsed":{"type":"mintTo","info":{"mint":"LP","amount":"1000"}}}]}}}
        lock={"transaction":{"message":{"instructions":[
          {"programId":"LOCK","parsed":{"type":"lock","info":{"lpMint":"LP","amount":"250","tokenAccount":"VAULT","unlockTime":1000+90*86400}}}]}}}
        def fake_rpc(url,method,params):
            if method=="getTokenSupply":return {"value":{"amount":"1000"}}
            if method=="getBlockTime":return 1000
            if method=="getAccountInfo":return {"value":{"data":{"parsed":{"info":{"mint":"OTHER","tokenAmount":{"amount":"250"}}}}}}
            raise AssertionError(method)
        with patch.object(solana_ingest,"rpc",side_effect=fake_rpc):
            burn,bv,locked,days,lv=solana_ingest.lp_proof("rpc",pool,"LP",{"LOCK"},5,lock)
        self.assertFalse(lv)
        self.assertEqual((locked,days),(0.0,0))

    def test_lp_supply_delta_without_explicit_burn_is_unverified(self):
        pool={"transaction":{"message":{"instructions":[
          {"parsed":{"type":"mintTo","info":{"mint":"LP","amount":"1000"}}}]}}}
        burn,bv,locked,days,lv=solana_ingest.lp_proof("rpc",pool,"LP",set(),5)
        self.assertEqual(burn,0.0)
        self.assertFalse(bv)
        self.assertEqual((locked,days,lv),(0.0,0,False))

    def test_lp_burn_requires_explicit_matching_burn_instruction(self):
        pool={"transaction":{"message":{"instructions":[
          {"parsed":{"type":"mintTo","info":{"mint":"LP","amount":"1000"}}}]}}}
        burn_tx={"meta":{"err":None},"transaction":{"message":{"instructions":[
          {"parsed":{"type":"burnChecked","info":{"mint":"LP","amount":"300"}}}]}}}
        burn,bv,locked,days,lv=solana_ingest.lp_proof("rpc",pool,"LP",set(),5,burn_tx)
        self.assertEqual(burn,30)
        self.assertTrue(bv)
        self.assertEqual((locked,days,lv),(0.0,0,False))

    def test_lp_burn_journal_survives_restart_and_deduplicates(self):
        tx={"meta":{"err":None},"transaction":{"signatures":["burn-sig"],"message":{"instructions":[
          {"parsed":{"type":"burnChecked","info":{"mint":"LP","amount":"25"}}}]}}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"burns.sqlite3"
            db=solana_ingest.open_burn_journal(path)
            self.assertTrue(solana_ingest.record_lp_burn(db,"LP",101,tx))
            self.assertTrue(solana_ingest.record_lp_burn(db,"LP",101,tx))
            db.close()
            db=solana_ingest.open_burn_journal(path)
            rows=solana_ingest.load_lp_burns(db,"LP",100,115)
            self.assertEqual(len(rows),1)
            self.assertEqual(solana_ingest.lp_burn_mints(rows[0]),{"LP"})
            db.close()

    def test_pool_association_is_restored_from_journal(self):
        launch={"slot":100,"mint_tx":{"transaction":{"signatures":["mint-sig"]}}}
        pool={"lp_mint":"LP","vaults":["VAULT"],"mints":{"MINT","QUOTE"}}
        pool_tx={"transaction":{"signatures":["pool-sig"]}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"burns.sqlite3"
            db=solana_ingest.open_burn_journal(path)
            solana_ingest.save_pool_association(db,"LP","MINT",launch,pool,pool_tx)
            db.close()
            db=solana_ingest.open_burn_journal(path)
            restored=solana_ingest.load_pool_associations(db)["LP"]
            self.assertEqual(restored[0],"MINT")
            self.assertEqual(restored[1],launch)
            self.assertEqual(restored[2]["mints"],pool["mints"])
            self.assertEqual(restored[3],pool_tx)
            db.close()
    def test_lp_burn_journal_rejects_failed_or_unrelated_transactions(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=solana_ingest.open_burn_journal(Path(tmp)/"burns.sqlite3")
            failed={"meta":{"err":{"InstructionError":[0,"Custom"]}},"transaction":{"signatures":["bad"],"message":{"instructions":[
              {"parsed":{"type":"burn","info":{"mint":"LP","amount":"10"}}}]}}}
            other={"meta":{"err":None},"transaction":{"signatures":["other"],"message":{"instructions":[
              {"parsed":{"type":"burn","info":{"mint":"OTHER","amount":"10"}}}]}}}
            self.assertFalse(solana_ingest.record_lp_burn(db,"LP",101,failed))
            self.assertFalse(solana_ingest.record_lp_burn(db,"LP",101,other))
            self.assertEqual(solana_ingest.load_lp_burns(db,"LP",0,200),[])
            db.close()
    def test_multiple_lp_burn_transactions_are_aggregated(self):
        pool={"meta":{"err":None},"transaction":{"message":{"instructions":[
          {"parsed":{"type":"mintTo","info":{"mint":"LP","amount":"1000"}}}]}}}
        burns=[{"meta":{"err":None},"transaction":{"message":{"instructions":[
          {"parsed":{"type":"burn","info":{"mint":"LP","amount":"200"}}}]}}},
          {"meta":{"err":None},"transaction":{"message":{"instructions":[
          {"parsed":{"type":"burnChecked","info":{"mint":"LP","amount":"300"}}}]}}}]
        burn,bv,_,_,_=solana_ingest.lp_proof("rpc",pool,"LP",set(),5,burns[-1],burns)
        self.assertEqual(burn,50)
        self.assertTrue(bv)

    def test_failed_lp_burn_transaction_is_not_counted(self):
        pool={"transaction":{"message":{"instructions":[
          {"parsed":{"type":"mintTo","info":{"mint":"LP","amount":"1000"}}}]}}}
        burn_tx={"meta":{"err":{"InstructionError":[0,"Custom"]}},"transaction":{"message":{"instructions":[
          {"parsed":{"type":"burn","info":{"mint":"LP","amount":"300"}}}]}}}
        burn,bv,_,_,_=solana_ingest.lp_proof("rpc",pool,"LP",set(),5,burn_tx)
        self.assertEqual(burn,0.0)
        self.assertFalse(bv)
    def test_websocket_receive_reassembles_fragmented_json(self):
        class FakeSocket:
            def __init__(self,data):
                self.data=data
                self.sent=[]
            def recv(self,n):
                part=self.data[:n]
                self.data=self.data[n:]
                return part
            def sendall(self,data):
                self.sent.append(data)
        payload=b'{"jsonrpc":"2.0","result":7}'
        split=11
        frames=bytes([0x01,split])+payload[:split]+bytes([0x80,len(payload)-split])+payload[split:]
        sock=FakeSocket(frames)
        self.assertEqual(solana_ingest.ws_recv(sock),{"jsonrpc":"2.0","result":7})

    def test_websocket_receive_handles_ping_between_fragments(self):
        class FakeSocket:
            def __init__(self,data):
                self.data=data
                self.sent=[]
            def recv(self,n):
                part=self.data[:n]
                self.data=self.data[n:]
                return part
            def sendall(self,data):
                self.sent.append(data)
        payload=b'{"ok":true}'
        split=5
        frames=bytes([0x01,split])+payload[:split]+bytes([0x89,1])+b"x"+bytes([0x80,len(payload)-split])+payload[split:]
        sock=FakeSocket(frames)
        self.assertEqual(solana_ingest.ws_recv(sock),{"ok":True})
        self.assertEqual(len(sock.sent),1)
    def test_launch_correlator_matches_pool_before_mint(self):
        c=solana_ingest.LaunchCorrelator()
        pool={"lp_mint":"LP","vaults":[],"mints":{"M"}}
        tx={"transaction":{"signatures":["pool-sig"]}}
        self.assertIsNone(c.add_pool("M",pool,tx,105,105))
        match=c.add_mint("M",{"slot":100,"mint_tx":{}},100)
        self.assertEqual(match,({"slot":100,"mint_tx":{}},pool,tx,105))
        self.assertNotIn("M",c.pools)

    def test_launch_correlator_matches_mint_before_pool_and_expires(self):
        c=solana_ingest.LaunchCorrelator(window=15)
        launch={"slot":100,"mint_tx":{}}
        pool={"lp_mint":"LP","vaults":[],"mints":{"M"}}
        self.assertIsNone(c.add_mint("M",launch,100))
        self.assertEqual(c.add_pool("M",pool,{},115,115),(launch,pool,{},115))
        self.assertIsNone(c.add_mint("STALE",{"slot":100,"mint_tx":{}},100))
        c.add_pool("OTHER",pool,{},116,116)
        self.assertNotIn("STALE",c.mints)

    def test_slot_metrics_counts_net_buys_and_launch_funders(self):
        def make(payer,delta):
            return {"transaction":{"message":{"accountKeys":[{"pubkey":payer,"signer":True}]}},
              "meta":{"preTokenBalances":[{"mint":"M","accountIndex":1,"owner":payer,"uiTokenAmount":{"amount":"0"}}],
              "postTokenBalances":[{"mint":"M","accountIndex":1,"owner":payer,"uiTokenAmount":{"amount":str(delta)}}]}}
        blocks={10:{"transactions":[make("A",100),make("B",200)]},
                11:{"transactions":[make("C",300)]}}
        with patch.object(solana_ingest,"rpc",side_effect=lambda u,m,p:blocks[p[0]]):
            r=solana_ingest.slot_metrics("rpc","M",10,11)
        self.assertEqual(r["slot0_buys"],2)
        self.assertEqual(r["total_early_buys"],3)
        self.assertEqual(r["unique_funders_in_slot0"],2)
        self.assertEqual(r["jito_bundle_count"],0)

if __name__=="__main__":unittest.main()
