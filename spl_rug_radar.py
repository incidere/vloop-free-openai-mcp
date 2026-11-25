"""spl_rug_radar.py: <=8KB real-time Solana SPL mint anomaly & rug-pull scorer (Python 3.12 stdlib)."""
from __future__ import annotations
import json, math, sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

BURN_PREFIXES = ("111111111111", "1nc1nerator1", "dead00000000")

def _sig(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))

def compute_gini(bals: Iterable[float], top_n: int = 10) -> Tuple[float, float, float]:
    vals = sorted((float(b) for b in bals if b > 0), reverse=True)
    if not vals:
        return 1.0, 1.0, 1.0
    tot = sum(vals)
    top = vals[:top_n]
    tsum = sum(top)
    t1, t10, k = top[0] / tot, tsum / tot, len(top)
    if k <= 1 or tsum <= 0:
        return 0.0, t1, t10
    num = sum((2 * (i + 1) - k - 1) * x for i, x in enumerate(sorted(top)))
    return max(0.0, min(1.0, num / (k * tsum))), t1, t10

@dataclass
class LaunchEvent:
    mint: str
    launch_slot: int
    event_slot: int
    mint_authority_active: bool = False
    freeze_authority_active: bool = False
    mutable_metadata: bool = False
    transfer_fee_bps: int = 0
    lp_burn_pct: float = 0.0
    lp_lock_pct: float = 0.0
    lp_lock_days: int = 0
    initial_liq_sol: float = 0.0
    fdv_sol: float = 100.0
    holders: Dict[str, float] = field(default_factory=dict)
    lp_vaults: Tuple[str, ...] = ()
    slot0_buys: int = 0
    total_early_buys: int = 1
    unique_funders_in_slot0: int = 1
    jito_bundle_count: int = 0
    uniform_buy_cv: float = 0.5
    wash_roundtrip_ratio: float = 0.0

def score_launch(ev: LaunchEvent) -> Dict[str, object]:
    age = max(0, ev.event_slot - ev.launch_slot)
    lp_set = set(ev.lp_vaults)
    clean = [b for a, b in ev.holders.items() if a not in lp_set and not a.startswith(BURN_PREFIXES)]
    gini, top1, top10 = compute_gini(clean, 10)
    auth = min(1.0, (0.45 if ev.mint_authority_active else 0.0) + (0.40 if ev.freeze_authority_active else 0.0)
               + (0.10 if ev.mutable_metadata else 0.0) + (min(0.35, (ev.transfer_fee_bps - 150) / 1000.0) if ev.transfer_fee_bps > 150 else 0.0))
    conc = min(1.0, 0.45 * max(0.0, top1 - 0.12) / 0.35 + 0.35 * max(0.0, top10 - 0.35) / 0.45 + 0.20 * gini)
    lp_safe = min(1.0, ev.lp_burn_pct / 100.0 + min(1.0, (ev.lp_lock_pct / 100.0) * min(1.0, ev.lp_lock_days / 90.0)))
    liq_r = ev.initial_liq_sol / max(1e-6, ev.fdv_sol)
    thin = 1.0 if liq_r < 0.03 else max(0.0, 1.0 - (liq_r - 0.03) / 0.12)
    lp = min(1.0, 0.72 * (1.0 - lp_safe) + 0.28 * thin)
    s0_r = ev.slot0_buys / max(1, ev.total_early_buys)
    coll = 1.0 - min(1.0, ev.unique_funders_in_slot0 / max(1, ev.slot0_buys))
    unif = max(0.0, 1.0 - ev.uniform_buy_cv / 0.35) if ev.uniform_buy_cv < 0.35 else 0.0
    bnd = min(1.0, 0.35 * s0_r * (0.3 + 0.7 * coll) + 0.25 * min(1.0, ev.jito_bundle_count / 3.0) * (0.2 + 0.8 * coll)
              + 0.20 * unif + 0.20 * min(1.0, ev.wash_roundtrip_ratio * 2.0))
    rug_p = round(_sig(-3.15 + 3.20 * auth + 2.35 * conc + 3.10 * lp + 2.45 * bnd), 4)
    mom_p = round(max(0.0, min(1.0, (1.0 - rug_p) * (0.55 + 0.45 * min(1.0, ev.total_early_buys / 40.0)))), 4)
    lbl = "RUG_HIGH" if rug_p >= 0.65 else ("WATCH" if rug_p >= 0.35 else "MOMENTUM_OK")
    return {"mint": ev.mint, "block_age": age, "rug_prob": rug_p, "momentum_prob": mom_p, "label": lbl,
            "components": {"auth_risk": round(auth, 4), "conc_risk": round(conc, 4), "lp_risk": round(lp, 4), "bundler_risk": round(bnd, 4)}}

def _run_selftest() -> int:
    import random
    rng = random.Random(20261126)
    preds: List[Tuple[float, int]] = []
    for i in range(100):
        rug = i < 50
        ev = LaunchEvent(
            mint=f"M{i:03d}", launch_slot=1000, event_slot=1000 + rng.randint(0, 14),
            mint_authority_active=(rng.random() < 0.7) if rug else False,
            freeze_authority_active=(rng.random() < 0.5) if rug else False,
            lp_burn_pct=rng.uniform(0.0, 20.0) if rug else rng.uniform(95.0, 100.0),
            lp_lock_pct=rng.uniform(0.0, 15.0) if rug else 0.0, lp_lock_days=rng.randint(0, 7) if rug else 0,
            initial_liq_sol=rng.uniform(1.0, 8.0) if rug else rng.uniform(25.0, 120.0),
            fdv_sol=rng.uniform(150.0, 900.0) if rug else rng.uniform(200.0, 600.0),
            holders=({f"w{k}": (450.0 if k == 0 else rng.uniform(5.0, 40.0)) for k in range(15)} if rug
                     else {"111111111111dead": 500.0, **{f"u{k}": rng.uniform(8.0, 35.0) for k in range(25)}}),
            slot0_buys=rng.randint(6, 14) if rug else rng.randint(2, 8),
            total_early_buys=rng.randint(14, 24) if rug else rng.randint(25, 80),
            unique_funders_in_slot0=rng.randint(1, 2) if rug else rng.randint(5, 10),
            jito_bundle_count=rng.randint(1, 4) if rug else rng.randint(0, 2),
            uniform_buy_cv=rng.uniform(0.02, 0.14) if rug else rng.uniform(0.40, 0.90),
            wash_roundtrip_ratio=rng.uniform(0.2, 0.7) if rug else rng.uniform(0.0, 0.05),
        )
        preds.append((float(score_launch(ev)["rug_prob"]), 1 if rug else 0))
    tp = sum(1 for p, y in preds if p >= 0.5 and y == 1)
    fp = sum(1 for p, y in preds if p >= 0.5 and y == 0)
    fn = sum(1 for p, y in preds if p < 0.5 and y == 1)
    tn = sum(1 for p, y in preds if p < 0.5 and y == 0)
    prec, rec = tp / max(1, tp + fp), tp / max(1, tp + fn)
    brier = sum((p - y) ** 2 for p, y in preds) / len(preds)
    ece = sum((len(g) / len(preds)) * abs(sum(p for p, _ in g) / len(g) - sum(y for _, y in g) / len(g))
              for b in range(5) if (g := [(p, y) for p, y in preds if (b * 0.2 <= p < (b + 1) * 0.2 or (b == 4 and p == 1.0))]))
    print(json.dumps({"n": len(preds), "tp": tp, "fp": fp, "tn": tn, "fn": fn,
                      "precision": round(prec, 4), "recall": round(rec, 4), "brier": round(brier, 4), "ece": round(ece, 4)}, indent=2))
    assert prec >= 0.95 and rec >= 0.95 and ece <= 0.15
    return 0

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_run_selftest())
    print(json.dumps(score_launch(LaunchEvent(mint="DemoMint", launch_slot=100, event_slot=108, lp_burn_pct=100.0,
                                              initial_liq_sol=40.0, fdv_sol=300.0, holders={"a": 20, "b": 18, "c": 15})), indent=2))
