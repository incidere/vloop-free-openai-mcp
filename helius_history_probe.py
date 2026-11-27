#!/usr/bin/env python3
"""Safely probe historical Solana JSON-RPC without printing the endpoint or credentials."""
import argparse
import json
import os
import time
import urllib.error
import urllib.request


def call(url, method, params, timeout):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
        elapsed = round((time.monotonic() - started) * 1000)
        if payload.get("error") is not None:
            return None, elapsed, "rpc_error"
        if "result" not in payload:
            return None, elapsed, "malformed_response"
        return payload["result"], elapsed, None
    except urllib.error.HTTPError as exc:
        # Never include the request URL or response body: providers may echo secrets.
        return None, round((time.monotonic() - started) * 1000), "http_" + str(exc.code)
    except (OSError, TimeoutError, ValueError):
        return None, round((time.monotonic() - started) * 1000), "transport_or_json_error"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slots", required=True,
                        help="comma-separated historical slots to inspect")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output", default="helius_history_probe.json")
    args = parser.parse_args()
    url = os.environ.get("HELIUS_RPC_URL")
    if not url:
        parser.error("HELIUS_RPC_URL secret is not configured")
    slots = [int(value.strip()) for value in args.slots.split(",") if value.strip()]
    if not slots or len(slots) > 20 or any(slot < 0 for slot in slots):
        parser.error("provide 1-20 non-negative slots")
    report = {"probe": "historical-jsonrpc-capability", "slots": [],
              "capabilities": {}, "credential_values_omitted": True}
    for method in ("getVersion", "getFirstAvailableBlock", "getSlot"):
        params = [{"commitment": "finalized"}] if method == "getSlot" else []
        result, ms, error = call(url, method, params, args.timeout)
        report["capabilities"][method] = {
            "ok": error is None, "latency_ms": ms,
            "result": result if error is None else None, "error": error}
    for slot in slots:
        row = {"slot": slot}
        produced, ms, error = call(url, "getBlocks", [slot, slot], args.timeout)
        row["getBlocks"] = {"ok": error is None, "latency_ms": ms,
                            "contains_slot": slot in (produced or []) if error is None else None,
                            "error": error}
        block = None
        for encoding in ("jsonParsed", "json"):
            block, ms, error = call(url, "getBlock", [slot, {
                "encoding": encoding, "transactionDetails": "full",
                "rewards": False, "maxSupportedTransactionVersion": 0
            }], args.timeout)
            row.setdefault("getBlock_attempts", []).append({
                "encoding": encoding, "ok": error is None and block is not None,
                "latency_ms": ms, "error": error})
            if error is None and block is not None:
                row["encoding_used"] = encoding
                txs = block.get("transactions") or []
                with_meta = sum(1 for tx in txs if tx.get("meta") is not None)
                token_rows = sum(
                    len((tx.get("meta") or {}).get(key) or [])
                    for tx in txs for key in ("preTokenBalances", "postTokenBalances"))
                row["block_summary"] = {
                    "block_time": block.get("blockTime"),
                    "transaction_count": len(txs),
                    "transactions_with_metadata": with_meta,
                    "token_balance_rows": token_rows,
                    "metadata_coverage": round(with_meta / len(txs), 4) if txs else None}
                break
        report["slots"].append(row)
    successful = sum(1 for row in report["slots"]
                     if row.get("block_summary", {}).get("transactions_with_metadata", 0) > 0)
    report["summary"] = {
        "requested_slots": len(slots),
        "blocks_with_transaction_metadata": successful,
        "warning": "Targeted samples do not establish exhaustive history, complete slot coverage, or sustained throughput."}
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    print("Credential values and endpoint URL were omitted from the report.")


if __name__ == "__main__":
    main()
