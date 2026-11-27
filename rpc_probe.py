#!/usr/bin/env python3
"""Rate-limit-aware probe for Solana Mainnet historical block/transaction RPC."""
import argparse
import json
import time
import urllib.error
import urllib.request


def call(url, method, params, timeout=30, retries=5):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    last = None
    for attempt in range(retries + 1):
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = json.loads(response.read())
            elapsed = round((time.monotonic() - started) * 1000)
            if "error" not in payload:
                return True, payload.get("result"), elapsed
            last = payload["error"]
            code = last.get("code") if isinstance(last, dict) else None
            if code != 429 and "rate" not in str(last).lower():
                return False, last, elapsed
        except urllib.error.HTTPError as exc:
            detail = exc.read(500).decode("utf-8", "replace")
            elapsed = round((time.monotonic() - started) * 1000)
            last = {"http_status": exc.code, "detail": detail}
            if exc.code != 429:
                return False, last, elapsed
        except Exception as exc:
            return False, {"exception": str(exc)}, round((time.monotonic() - started) * 1000)
        if attempt < retries:
            delay = min(30, 2 ** attempt)
            print(f"  rate limited on {method}; waiting {delay}s (retry {attempt + 1}/{retries})", flush=True)
            time.sleep(delay)
    return False, last, 0


def find_block_time(url, slot):
    """Find a nearby produced block and its timestamp, handling skipped slots."""
    for offset in range(0, 10001, 1000):
        candidate = max(0, slot - offset)
        ok, value, _ = call(url, "getBlockTime", [candidate], retries=2)
        if ok and value is not None:
            return candidate, value
    raise RuntimeError(f"could not obtain block time near slot {slot}")


def find_slot_for_timestamp(url, target_time, current_slot):
    """Binary-search slot space using RPC block timestamps, not a fixed slot rate."""
    low, high = 0, current_slot
    best = None
    while low <= high:
        mid = (low + high) // 2
        slot, timestamp = find_block_time(url, mid)
        best = (slot, timestamp)
        if timestamp < target_time:
            low = mid + 1
        elif timestamp > target_time:
            high = mid - 1
        else:
            break
        if high - low < 32:
            break
    # Search the narrow remaining interval and choose the closest produced block.
    candidates = []
    for probe in (low, high, (low + high) // 2):
        if probe < 0:
            continue
        slot, timestamp = find_block_time(url, probe)
        candidates.append((abs(timestamp - target_time), slot, timestamp))
    if best is not None:
        candidates.append((abs(best[1] - target_time), best[0], best[1]))
    _, slot, timestamp = min(candidates)
    return slot, timestamp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc", default="https://api.mainnet-beta.solana.com")
    parser.add_argument("--days", nargs="+", type=int, default=[1, 7, 30, 90])
    parser.add_argument("--window", type=int, default=256, help="slot radius around each target date")
    args = parser.parse_args()

    print("RPC:", args.rpc)
    for method, params in (
        ("getHealth", []),
        ("getSlot", [{"commitment": "finalized"}]),
        ("getFirstAvailableBlock", []),
    ):
        ok, result, ms = call(args.rpc, method, params)
        print(f"{method}: {'OK' if ok else 'FAIL'} ({ms} ms): {str(result)[:400]}")
        if method == "getSlot" and ok:
            current_slot = result
        if method == "getFirstAvailableBlock" and ok:
            first_slot = result
    if "current_slot" not in locals():
        raise SystemExit("Could not obtain finalized slot; verify the endpoint and network.")

    try:
        anchor_slot, anchor_time = find_block_time(args.rpc, current_slot)
        print(f"current_anchor: slot={anchor_slot} blockTime={anchor_time}")
    except RuntimeError as exc:
        raise SystemExit(f"Cannot obtain current block time: {exc}")

    for days in args.days:
        # Find the target by timestamp so long-range slot-rate drift cannot skew the date.
        target_time = anchor_time - days * 86400
        try:
            target, target_block_time = find_slot_for_timestamp(args.rpc, target_time, anchor_slot)
        except RuntimeError as exc:
            print(f"history_{days}d: INCONCLUSIVE timestamp search failed: {exc}")
            continue
        lo, hi = max(0, target - args.window), target + args.window
        ok, slots, ms = call(args.rpc, "getBlocks", [lo, hi])
        if not ok:
            print(f"history_{days}d: INCONCLUSIVE getBlocks failed: {str(slots)[:300]}")
            continue
        if not slots:
            print(f"history_{days}d: NO PRODUCED BLOCK in slot range {lo}-{hi}; try a wider --window")
            continue
        slot = min(slots, key=lambda value: abs(value - target))
        ok, block, block_ms = call(args.rpc, "getBlock", [slot, {
            "encoding": "json", "transactionDetails": "signatures", "rewards": False,
            "maxSupportedTransactionVersion": 1
        }])
        if not ok or not block:
            print(f"history_{days}d: INCONCLUSIVE getBlock slot={slot} failed: {str(block)[:300]}")
            continue
        # transactionDetails="signatures" returns a top-level signatures array;
        # it does not return a transactions array.
        signatures = block.get("signatures", [])
        signature = signatures[0] if signatures else None
        if not signature:
            print(f"history_{days}d: BLOCK_OK slot={slot} tx_count=0 but no sample signature")
            continue
        tx_ok, tx_result, tx_ms = call(args.rpc, "getTransaction", [signature, {
            "encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 1
        }])
        block_time = block.get("blockTime")
        actual_age = ((anchor_time - block_time) / 86400) if block_time is not None else None
        age_text = f" actual_age_days={actual_age:.2f}" if actual_age is not None else " actual_age_days=unknown"
        print(f"history_{days}d: BLOCK_OK slot={slot} blockTime={block_time}{age_text} "
              f"tx_count={len(signatures)} getBlocks={ms}ms getBlock={block_ms}ms "
              f"getTransaction={'OK' if tx_ok else 'FAIL'} ({tx_ms}ms)")

    if "first_slot" in locals():
        print(f"retention_note: getFirstAvailableBlock={first_slot}; this alone does not prove full archival coverage.")
    print("Probe scope: one sampled block and one transaction near each requested age. "
          "This does not establish complete history, high-volume capacity, or unlimited retention.")


if __name__ == "__main__":
    main()
