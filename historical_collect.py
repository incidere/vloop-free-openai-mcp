#!/usr/bin/env python3
"""Bounded, resumable historical SPL mint-initialization discovery.

This is a sampling collector, not an exhaustive chain indexer. It scans produced
blocks in a requested age window, optionally samples at most --max-blocks, and
writes mint-initialization candidates with signatures and source slots to JSONL.
"""
import argparse
import http.client
import json
import os
import random
import time
import urllib.error
import urllib.request

from solana_ingest import launch_mints

RPC_DEFAULT = "https://api.mainnet-beta.solana.com"


def rpc(url, method, params, retries=5):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                payload = json.loads(response.read())
            if "error" not in payload:
                return payload.get("result")
            last = payload["error"]
            if last.get("code") != 429 and "rate" not in str(last).lower():
                raise RuntimeError(last)
        except urllib.error.HTTPError as exc:
            last = {"http_status": exc.code, "detail": exc.read(500).decode("utf-8", "replace")}
            if exc.code != 429:
                raise RuntimeError(last) from exc
        except http.client.IncompleteRead as exc:
            # Public RPC responses can be several MB; a dropped connection may
            # leave a partial body. Retry the same request rather than marking
            # the slot complete or losing the checkpoint.
            last = {"incomplete_read": str(exc), "partial_bytes": len(exc.partial or b"")}
        except (http.client.HTTPException, TimeoutError, OSError) as exc:
            last = {"exception": str(exc)}
        if attempt < retries:
            delay = min(30, 2 ** attempt)
            print(f"rate limited/transport error on {method}; retrying in {delay}s", flush=True)
            time.sleep(delay)
    raise RuntimeError(f"{method} failed after retries: {last}")


def block_time(url, slot):
    # getBlockTime can return null for skipped slots. Walk backwards locally.
    for back in range(0, 2001, 100):
        value = rpc(url, "getBlockTime", [max(0, slot - back)], retries=2)
        if value is not None:
            return max(0, slot - back), value
    raise RuntimeError(f"no block timestamp found near slot {slot}")


def find_slot_at_time(url, target_time, high):
    lo, hi = 0, high
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        slot, ts = block_time(url, mid)
        candidate = (abs(ts - target_time), slot, ts)
        if best is None or candidate < best:
            best = candidate
        if ts < target_time:
            lo = mid + 1
        elif ts > target_time:
            hi = mid - 1
        else:
            break
        if hi - lo <= 4:
            break
    for probe in range(max(0, lo - 3), min(high, hi + 3) + 1):
        slot, ts = block_time(url, probe)
        candidate = (abs(ts - target_time), slot, ts)
        if candidate < best:
            best = candidate
    return best[1], best[2]


def sampled_slot_numbers(start, end, count, seed):
    """Choose bounded candidate slots without first indexing the whole range.

    Some sampled slots may be skipped slots; getBlock(None) handles those.
    The number returned never exceeds count.
    """
    if end < start or count < 1:
        return []
    population = end - start + 1
    rng = random.Random(seed)
    if count >= population:
        return list(range(start, end + 1))
    return sorted(rng.sample(range(start, end + 1), count))


def produced_slots(url, start, end, chunk_slots):
    """Enumerate every produced slot; intended only for explicit exhaustive mode."""
    slots = []
    for left in range(start, end + 1, chunk_slots):
        right = min(end, left + chunk_slots - 1)
        result = rpc(url, "getBlocks", [left, right])
        slots.extend(result or [])
        print(f"indexed slot range {left}-{right}: {len(result or [])} produced blocks", flush=True)
    return sorted(set(slots))


def read_checkpoint(path):
    if not path or not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return set(data.get("completed_slots", []))


def write_checkpoint(path, completed):
    if path:
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump({"completed_slots": sorted(completed)}, handle)
        os.replace(temp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rpc", default=os.getenv("SOLANA_RPC_URL", RPC_DEFAULT))
    ap.add_argument("--days", type=float, default=1.0, help="lookback window in days")
    ap.add_argument("--max-blocks", type=int, default=100, help="maximum blocks to fetch; 0 means all produced blocks")
    ap.add_argument("--chunk-slots", type=int, default=5000)
    ap.add_argument("--out", default="historical_mint_candidates.jsonl")
    ap.add_argument("--checkpoint", default="historical_mint_checkpoint.json")
    ap.add_argument("--seed", type=int, default=7, help="deterministic sampling seed")
    args = ap.parse_args()
    if args.days <= 0 or args.max_blocks < 0 or args.chunk_slots < 1:
        ap.error("--days and --chunk-slots must be positive; --max-blocks cannot be negative")

    current = rpc(args.rpc, "getSlot", [{"commitment": "finalized"}])
    anchor_slot, anchor_time = block_time(args.rpc, current)
    target_slot, target_time = find_slot_at_time(args.rpc, anchor_time - args.days * 86400, anchor_slot)
    start, end = min(target_slot, anchor_slot), max(target_slot, anchor_slot)
    print(f"window: requested_days={args.days:g} start_slot={start} end_slot={end} "
          f"actual_days={(anchor_time-target_time)/86400:.3f}", flush=True)

    if args.max_blocks:
        slots = sampled_slot_numbers(start, end, args.max_blocks, args.seed)
        print(
            f"sampling at most {len(slots)} candidate slot numbers from the window "
            f"(seed={args.seed}); skipped slots may reduce fetched block count; "
            "not exhaustive",
            flush=True,
        )
    else:
        slots = produced_slots(args.rpc, start, end, args.chunk_slots)
        print(
            f"exhaustive mode: indexed {len(slots)} produced slots; "
            "this may require many RPC requests",
            flush=True,
        )
    completed = read_checkpoint(args.checkpoint)
    todo = [slot for slot in slots if slot not in completed]
    known = set()
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    known.add((row["signature"], row["mint"]))
                except (ValueError, KeyError):
                    continue

    scanned = 0
    with open(args.out, "a", encoding="utf-8") as output:
        for slot in todo:
            try:
                block = rpc(args.rpc, "getBlock", [slot, {
                    "encoding": "jsonParsed", "transactionDetails": "full",
                    "rewards": False, "maxSupportedTransactionVersion": 1
                }])
                if not block:
                    completed.add(slot)
                    continue
                for entry in block.get("transactions", []):
                    tx = entry.get("transaction", {})
                    signatures = tx.get("signatures", [])
                    if not signatures:
                        continue
                    signature = signatures[0]
                    for mint in launch_mints(entry):
                        key = (signature, mint)
                        if key in known:
                            continue
                        row = {
                            "mint": mint, "launch_slot": slot,
                            "block_time": block.get("blockTime"),
                            "signature": signature,
                            "source": "solana-jsonrpc-getBlock-jsonParsed",
                            "label": None,
                            "status": "candidate_requires_pool_and_outcome_review"
                        }
                        output.write(json.dumps(row, separators=(",", ":")) + "\n")
                        output.flush()
                        known.add(key)
                completed.add(slot)
                scanned += 1
                if scanned % 10 == 0:
                    write_checkpoint(args.checkpoint, completed)
                    print(f"scanned={scanned}/{len(todo)} candidates={len(known)}", flush=True)
            except Exception as exc:
                print(f"slot {slot} failed; leaving it pending for resume: {exc}", flush=True)
                write_checkpoint(args.checkpoint, completed)
                continue
    write_checkpoint(args.checkpoint, completed)
    print(f"done: scanned_this_run={scanned} completed_total={len(completed)} "
          f"candidate_rows={len(known)} output={args.out}")
    print("Important: block sampling can miss launches. Candidates are not labeled and are not backtest-ready.")


if __name__ == "__main__":
    main()
