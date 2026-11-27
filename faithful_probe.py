#!/usr/bin/env python3
"""Probe a Yellowstone Old Faithful-compatible Solana JSON-RPC endpoint.

Checks slot-addressed historical methods and whether jsonParsed is enabled.
A successful sample is not evidence of exhaustive archive completeness.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def rpc(url, method, params, timeout=60):
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params
    }).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except (OSError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"transport/JSON error: {exc}") from exc
    if payload.get("error") is not None:
        raise RuntimeError(f"RPC error: {payload['error']}")
    if "result" not in payload:
        raise RuntimeError(f"malformed JSON-RPC response: {payload}")
    return payload["result"]


def inspect_block(block):
    if block is None:
        return {"status": "NULL", "transaction_count": 0,
                "metadata_count": 0, "token_balance_rows": 0}
    transactions = block.get("transactions") or []
    metadata_count = sum(1 for row in transactions if row.get("meta") is not None)
    token_rows = sum(
        len((row.get("meta") or {}).get(key) or [])
        for row in transactions
        for key in ("preTokenBalances", "postTokenBalances")
    )
    return {
        "status": "OK",
        "block_time": block.get("blockTime"),
        "parent_slot": block.get("parentSlot"),
        "transaction_count": len(transactions),
        "metadata_count": metadata_count,
        "transactions_with_metadata": metadata_count,
        "token_balance_rows": token_rows,
        "sample_signature": (
            (transactions[0].get("transaction") or {}).get("signatures") or [None]
        )[0] if transactions else None,
    }


def probe(url, slots, address=None, timeout=60):
    call = lambda method, params: rpc(url, method, params, timeout)
    report = {"endpoint": url, "capabilities": {}, "slots": [], "address_history": None}
    for method, params in (
        ("getVersion", []),
        ("getFirstAvailableBlock", []),
        ("getSlot", [{"commitment": "finalized"}]),
    ):
        try:
            report["capabilities"][method] = {"ok": True, "result": call(method, params)}
        except RuntimeError as exc:
            report["capabilities"][method] = {"ok": False, "error": str(exc)}

    for slot in slots:
        row = {"requested_slot": slot}
        try:
            produced = call("getBlocks", [slot, slot])
            row["produced"] = slot in (produced or [])
        except RuntimeError as exc:
            row["produced"] = None
            row["getBlocks_error"] = str(exc)
        try:
            row["block_time"] = call("getBlockTime", [slot])
        except RuntimeError as exc:
            row["block_time_error"] = str(exc)

        block = None
        for encoding in ("jsonParsed", "json"):
            try:
                block = call("getBlock", [slot, {
                    "encoding": encoding,
                    "transactionDetails": "full",
                    "rewards": False,
                    "maxSupportedTransactionVersion": 0,
                }])
                row["encoding_used"] = encoding
                row["json_parsed_supported"] = encoding == "jsonParsed"
                row["block"] = inspect_block(block)
                break
            except RuntimeError as exc:
                row.setdefault("block_encoding_errors", {})[encoding] = str(exc)

        if block is not None:
            sample = row["block"]["sample_signature"]
            if sample:
                try:
                    tx = call("getTransaction", [sample, {
                        "encoding": "json",
                        "maxSupportedTransactionVersion": 0,
                    }])
                    row["sample_transaction"] = {
                        "ok": tx is not None,
                        "slot": tx.get("slot") if isinstance(tx, dict) else None,
                        "meta_present": bool(tx and tx.get("meta") is not None),
                    }
                except RuntimeError as exc:
                    row["sample_transaction"] = {"ok": False, "error": str(exc)}
        report["slots"].append(row)

    if address:
        try:
            result = call("getSignaturesForAddress", [address, {"limit": 5}])
            report["address_history"] = {
                "ok": True, "returned": len(result or []),
                "note": "A small successful sample does not prove full address-history coverage."
            }
        except RuntimeError as exc:
            report["address_history"] = {
                "ok": False, "error": str(exc),
                "note": "Old Faithful documentation notes remote GSFA indexes cannot currently provide full getSignaturesForAddress support."
            }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc", default=os.getenv("OLD_FAITHFUL_RPC_URL"),
                        help="Old Faithful JSON-RPC URL (or OLD_FAITHFUL_RPC_URL)")
    parser.add_argument("--slot", type=int, action="append", required=True,
                        help="historical slot to probe; repeat for multiple slots")
    parser.add_argument("--address", help="optional diagnostic GSFA lookup")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    if not args.rpc:
        parser.error("--rpc or OLD_FAITHFUL_RPC_URL is required")
    if any(slot < 0 for slot in args.slot):
        parser.error("slots must be non-negative")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    result = probe(args.rpc, args.slot, args.address, args.timeout)
    print(json.dumps(result, indent=2, sort_keys=True))
    good = sum(1 for row in result["slots"]
               if row.get("block", {}).get("status") == "OK"
               and row.get("block", {}).get("transactions_with_metadata", 0) > 0)
    print(f"\nsummary: {good}/{len(args.slot)} requested slots returned blocks with transaction metadata")
    print("This is a targeted capability probe, not proof of exhaustive historical coverage.")


if __name__ == "__main__":
    main()
