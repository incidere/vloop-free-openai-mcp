#!/usr/bin/env python3
"""Solana SPL Mint Anomaly & Rug-Pull Radar Scorer (Python 3.12 stdlib, <=8KB)."""
import sys, json, math
from dataclasses import dataclass, field
from typing import Any

BURN_PREFIXES = ("11111111111111111111111111111111", "Incinerator", "dead", "burn")

@dataclass
class LaunchEvent:
    mint: str; launch_slot: int; event_slot: int
    mint_authority_active: bool = False; freeze_authority_active: bool = False
    mutable_metadata: bool = False; transfer_fee_bps: int = 0
    holders: dict[str, float] = field(default_factory=dict)
    lp_vaults: list[str] = field(default_factory=list)
    lp_burn_pct: float = 0.0; lp_lock_pct: float = 0.0; lp_lock_days: int = 0
    initial_liq_sol: float = 0.0; fdv_sol: float = 0.0
    slot0_buys: int = 0; total_early_buys: int = 0
    unique_funders_in_slot0: int = 1; jito_bundle_count: int = 0
    uniform_buy_cv: float = 0.5; wash_roundtrip_ratio: float = 0.0

def compute_gini(bals: list[float], top_n: int = 10) -> tuple[float, float, float]:
    f = sorted([float(b) for b in bals if b > 0], reverse=True)
    if not f: return 0.0, 0.0, 0.0
    tot, top1, top_n_share, n = sum(f), f[0] / sum(f), sum(f[:top_n]) / sum(f), len(f)
    if n == 1: return 1.0, 1.0, 1.0
    numer = sum((i + 1) * y for i, y in enumerate(f[::-1]))
    return max(0.0, min(1.0, (2.0 * numer) / (n * tot) - (n + 1.0) / n)), top1, top_n_share

def _sig(z: float) -> float:
    return 0.0 if z < -35.0 else (1.0 if z > 35.0 else 1.0 / (1.0 + math.exp(-z)))

def score_launch(ev: LaunchEvent) -> dict[str, Any]:
    age, in_win = max(0, ev.event_slot - ev.launch_slot), max(0, ev.event_slot - ev.launch_slot) <= 15
    flags: list[str] = [] if in_win else ["EXCEEDS_15_BLOCK_WINDOW"]

    auth = min(1.0, 0.45 * ev.mint_authority_active + 0.40 * ev.freeze_authority_active + 0.10 * ev.mutable_metadata + (min(0.35, (ev.transfer_fee_bps - 100) / 1000.0) if ev.transfer_fee_bps > 100 else 0.0))
    if ev.mint_authority_active: flags.append("MINT_AUTH_ACTIVE")
    if ev.freeze_authority_active: flags.append("FREEZE_AUTH_ACTIVE")
    if ev.transfer_fee_bps > 100: flags.append(f"TRANSFER_FEE_{ev.transfer_fee_bps}BPS")

    lp_set = set(ev.lp_vaults)
    clean = [b for a, b in ev.holders.items() if a not in lp_set and not any(a.lower().startswith(p.lower()) for p in BURN_PREFIXES)]
    gini, top1, top10 = compute_gini(clean, 10)
    conc = min(1.0, 0.45 * max(0.0, top1 - 0.12) / 0.35 + 0.35 * max(0.0, top10 - 0.35) / 0.45 + 0.20 * gini)
    if top1 > 0.25: flags.append(f"WHALE_TOP1_{round(top1 * 100, 1)}PCT")
    if gini > 0.70: flags.append(f"HIGH_GINI_{round(gini, 3)}")

    lp_safe = min(1.0, ev.lp_burn_pct / 100.0 + (ev.lp_lock_pct / 100.0) * min(1.0, ev.lp_lock_days / 90.0))
    liq_r = ev.initial_liq_sol / max(1e-6, ev.fdv_sol)
    thin = 1.0 if liq_r < 0.03 else max(0.0, 1.0 - (liq_r - 0.03) / 0.12)
    lp = min(1.0, 0.72 * (1.0 - lp_safe) + 0.28 * thin)
    if lp_safe < 0.80: flags.append("LP_UNPROTECTED")
    if thin > 0.50: flags.append("THIN_LIQUIDITY")

    s0_r, coll = ev.slot0_buys / max(1, ev.total_early_buys), 1.0 - min(1.0, ev.unique_funders_in_slot0 / max(1, ev.slot0_buys))
    unif = max(0.0, 1.0 - ev.uniform_buy_cv / 0.35) if ev.uniform_buy_cv < 0.35 else 0.0
    bnd = min(1.0, 0.35 * s0_r * (0.3 + 0.7 * coll) + 0.25 * min(1.0, ev.jito_bundle_count / 3.0) * (0.2 + 0.8 * coll)
              + 0.20 * unif + 0.20 * min(1.0, ev.wash_roundtrip_ratio * 2.0))
    if coll > 0.60 and ev.slot0_buys >= 3: flags.append("SLOT0_BUNDLER_COLLUSION")

    rug_p = round(_sig(-3.20 + 3.25 * auth + 2.40 * conc + 3.15 * lp + 2.50 * bnd), 4)
    mom_p = round(max(0.0, min(1.0, (1.0 - rug_p) * (0.50 + 0.50 * min(1.0, ev.total_early_buys / 35.0)))), 4)
    lbl = "CRITICAL" if rug_p >= 0.70 else ("HIGH" if rug_p >= 0.45 else ("MEDIUM" if rug_p >= 0.20 else "LOW"))

    return {
        "mint": ev.mint, "block_age": age, "in_window": in_win, "rug_probability": rug_p,
        "momentum_probability": mom_p, "risk_level": lbl, "flags": flags,
        "components": {k: round(v, 4) for k, v in [
            ("auth_risk", auth), ("concentration_risk", conc), ("lp_risk", lp), ("bundler_risk", bnd),
            ("gini", gini), ("top1_share", top1), ("top10_share", top10)
        ]}
    }

def parse_event(d: dict[str, Any]) -> LaunchEvent:
    h = d.get("holders") or {}
    h = {f"h{i}": float(v) for i, v in enumerate(h)} if isinstance(h, list) else {str(k): float(v) for k, v in h.items()}
    kw = {k: v for k, v in d.items() if k in LaunchEvent.__dataclass_fields__}
    kw.update(mint=str(d.get("mint", "unknown")), launch_slot=int(d.get("launch_slot", 0)), event_slot=int(d.get("event_slot", 0)),
              holders=h, lp_vaults=list(d.get("lp_vaults", [])))
    if "mint_authority" in d and "mint_authority_active" not in kw: kw["mint_authority_active"] = bool(d["mint_authority"])
    if "freeze_authority" in d and "freeze_authority_active" not in kw: kw["freeze_authority_active"] = bool(d["freeze_authority"])
    if d.get("lp_burned") and "lp_burn_pct" not in kw: kw["lp_burn_pct"] = 100.0
    if d.get("lp_locked") and "lp_lock_pct" not in kw: kw["lp_lock_pct"], kw["lp_lock_days"] = 100.0, 90
    if "tx_slots" in d and "slot0_buys" not in kw: kw["slot0_buys"] = len(d["tx_slots"])
    return LaunchEvent(**kw)

def run_selftest() -> int:
    import random
    rng = random.Random(20261126)
    preds: list[tuple[float, int]] = []
    for i in range(100):
        rug = i < 50
        ev = LaunchEvent(
            mint=f"M_{i:03d}", launch_slot=1000, event_slot=1000 + rng.randint(0, 14),
            mint_authority_active=rng.random() < 0.75, freeze_authority_active=rng.random() < 0.50,
            lp_burn_pct=rng.uniform(0, 15), initial_liq_sol=rng.uniform(1, 5), fdv_sol=rng.uniform(200, 800),
            holders={"dev": 550.0, **{f"w{k}": rng.uniform(10, 30) for k in range(14)}},
            slot0_buys=rng.randint(6, 12), total_early_buys=rng.randint(12, 20),
            unique_funders_in_slot0=1, jito_bundle_count=rng.randint(2, 5),
            uniform_buy_cv=rng.uniform(0.01, 0.1), wash_roundtrip_ratio=rng.uniform(0.25, 0.65)
        ) if rug else LaunchEvent(
            mint=f"M_{i:03d}", launch_slot=1000, event_slot=1000 + rng.randint(0, 14),
            lp_burn_pct=rng.uniform(95, 100), initial_liq_sol=rng.uniform(30, 150), fdv_sol=rng.uniform(200, 500),
            holders={BURN_PREFIXES[0]: 500.0, **{f"u{k}": rng.uniform(10, 30) for k in range(25)}},
            slot0_buys=rng.randint(1, 4), total_early_buys=rng.randint(25, 70),
            unique_funders_in_slot0=rng.randint(5, 10), uniform_buy_cv=rng.uniform(0.4, 0.85)
        )
        preds.append((float(score_launch(ev)["rug_probability"]), 1 if rug else 0))

    tp = sum(1 for p, y in preds if p >= 0.5 and y)
    fp = sum(1 for p, y in preds if p >= 0.5 and not y)
    fn = sum(1 for p, y in preds if p < 0.5 and y)
    tn = sum(1 for p, y in preds if p < 0.5 and not y)
    prec, rec = tp / max(1, tp + fp), tp / max(1, tp + fn)
    brier = sum((p - y) ** 2 for p, y in preds) / 100.0
    ece = sum((len(g) / 100.0) * abs(sum(p for p, _ in g) / len(g) - sum(y for _, y in g) / len(g))
              for b in range(5) if (g := [pair for pair in preds if b * 0.2 <= pair[0] < (b + 1) * 0.2 or (b == 4 and pair[0] == 1.0)]))
    m = {"n": 100, "tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": round(prec, 4), "recall": round(rec, 4), "brier": round(brier, 4), "ece": round(ece, 4)}
    print(json.dumps(m, indent=2))
    assert prec >= 0.95 and rec >= 0.95 and ece <= 0.15, f"Gate failed: {m}"
    return 0

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(run_selftest())
    print(json.dumps(score_launch(LaunchEvent(
        mint="DemoMint", launch_slot=100, event_slot=105,
        lp_burn_pct=100.0, initial_liq_sol=50.0, fdv_sol=200.0, holders={"dead": 800.0, "user1": 20.0}, total_early_buys=45
    )), indent=2))
