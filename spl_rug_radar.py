"""Dependency-free SPL launch risk scorer. Python 3.12; input is normalized chain data."""
import json, math, sys
from dataclasses import dataclass, asdict, field
BURN = {'11111111111111111111111111111111', '1nc1nerator11111111111111111111111'}
MAX_AGE = 15

def _num(x, name, lo=0.0, hi=float('inf')):
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise ValueError(name + ' must be numeric')
    if not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(name + ' out of range')
    return v

def compute_gini(holders):
    """Full-distribution Gini; do not calculate inequality on the top-10 subset."""
    vals = sorted((_num(v, 'holder balance') for v in holders if float(v) > 0))
    n = len(vals)
    if not n:
        return 0.0
    total = sum(vals)
    return max(0.0, min(1.0, 2 * sum(((i + 1) * v for i, v in enumerate(vals))) / (n * total) - (n + 1) / n))

@dataclass
class LaunchEvent:
    mint: str
    launch_slot: int
    event_slot: int
    mint_authority_active: bool = False
    freeze_authority_active: bool = False
    metadata_mutable: bool = False
    transfer_fee_bps: int = 0
    holders: dict = field(default_factory=dict)
    lp_vaults: list = field(default_factory=list)
    lp_burn_pct: float = 0.0
    lp_lock_pct: float = 0.0
    lp_lock_days: int = 0
    lp_burn_verified: bool = False
    lp_lock_verified: bool = False
    initial_liq_sol: float = 0.0
    fdv_sol: float = 0.0
    slot0_buys: int = 0
    total_early_buys: int = 0
    unique_funders_in_slot0: int = 0
    jito_bundle_count: int = 0
    buy_amount_cv: float = 1.0
    wash_roundtrip_ratio: float = 0.0

def parse_event(raw):
    """Normalize a decoded mint/pool event. Unknown LP proof stays unverified."""
    if not isinstance(raw, dict):
        raise ValueError('event must be an object')
    h = raw.get('holders') or {}
    if isinstance(h, list):
        h = {str(i): v for i, v in enumerate(h)}
    if not isinstance(h, dict):
        raise ValueError('holders must be an object or list')
    d = dict(raw)
    aliases = {'mint_authority': 'mint_authority_active', 'freeze_authority': 'freeze_authority_active', 'mutable_metadata': 'metadata_mutable'}
    for old, new in aliases.items():
        if old in d and new not in d:
            d[new] = d[old]
    if d.get('lp_burned') is True and 'lp_burn_verified' not in d:
        d['lp_burn_verified'] = False
    allowed = LaunchEvent.__dataclass_fields__
    d = {k: v for k, v in d.items() if k in allowed}
    d['mint'] = str(d.get('mint', 'unknown'))
    d['launch_slot'] = int(d.get('launch_slot', 0))
    d['event_slot'] = int(d.get('event_slot', 0))
    d['holders'] = h
    return LaunchEvent(**d)

def score_launch(ev):
    if isinstance(ev, dict):
        ev = parse_event(ev)
    if not isinstance(ev, LaunchEvent):
        raise TypeError('expected LaunchEvent or dict')
    age = ev.event_slot - ev.launch_slot
    if age < 0:
        raise ValueError('event_slot precedes launch_slot')
    in_window = age <= MAX_AGE
    if not in_window:
        return {'mint': ev.mint, 'block_age': age, 'in_window': False, 'rug_probability': None, 'momentum_probability': None, 'risk_level': 'OUT_OF_WINDOW', 'flags': ['EXCEEDS_15_BLOCK_WINDOW'], 'calibration': 'heuristic; not trained on labeled market data'}
    for name, val, hi in (('transfer_fee_bps', ev.transfer_fee_bps, 10000), ('lp_burn_pct', ev.lp_burn_pct, 100), ('lp_lock_pct', ev.lp_lock_pct, 100), ('lp_lock_days', ev.lp_lock_days, 36500), ('initial_liq_sol', ev.initial_liq_sol, float('inf')), ('fdv_sol', ev.fdv_sol, float('inf')), ('slot0_buys', ev.slot0_buys, 10 ** 9), ('total_early_buys', ev.total_early_buys, 10 ** 9), ('unique_funders_in_slot0', ev.unique_funders_in_slot0, 10 ** 9), ('jito_bundle_count', ev.jito_bundle_count, 10 ** 9), ('buy_amount_cv', ev.buy_amount_cv, 1000000.0), ('wash_roundtrip_ratio', ev.wash_roundtrip_ratio, 1)):
        _num(val, name, 0, hi)
    balances = {str(a): _num(b, 'holder balance') for a, b in ev.holders.items()}
    excluded = set(ev.lp_vaults) | BURN
    clean = [v for a, v in balances.items() if a not in excluded and (not a.lower().startswith(('dead', 'burn', 'incinerator')))]
    total = sum(clean)
    shares = sorted((v / total for v in clean), reverse=True) if total else []
    top1 = shares[0] if shares else 0.0
    top10 = sum(shares[:10])
    gini = compute_gini(clean)
    flags = []
    auth = 0.4 * bool(ev.mint_authority_active) + 0.35 * bool(ev.freeze_authority_active) + 0.1 * bool(ev.metadata_mutable) + min(0.15, max(0, ev.transfer_fee_bps - 100) / 1000)
    if ev.mint_authority_active:
        flags.append('MINT_AUTHORITY_ACTIVE')
    if ev.freeze_authority_active:
        flags.append('FREEZE_AUTHORITY_ACTIVE')
    if ev.metadata_mutable:
        flags.append('MUTABLE_METADATA')
    if ev.transfer_fee_bps > 100:
        flags.append('HIGH_TRANSFER_FEE')
    conc = min(1, 0.45 * max(0, top1 - 0.12) / 0.35 + 0.35 * max(0, top10 - 0.35) / 0.45 + 0.2 * gini)
    if top1 > 0.25:
        flags.append('TOP1_WHALE')
    if gini > 0.7:
        flags.append('HIGH_HOLDER_GINI')
    burn_proof = ev.lp_burn_pct if ev.lp_burn_verified else 0.0
    lock_proof = ev.lp_lock_pct * min(1, ev.lp_lock_days / 90) if ev.lp_lock_verified else 0.0
    lp_protection = min(1, burn_proof / 100 + lock_proof / 100)
    liq_ratio = ev.initial_liq_sol / ev.fdv_sol if ev.fdv_sol else 0.0
    thin = (1.0 if liq_ratio < 0.03 else max(0, 1 - (liq_ratio - 0.03) / 0.12)) if ev.initial_liq_sol and ev.fdv_sol else 0.0
    if not ev.initial_liq_sol or not ev.fdv_sol:
        flags.append('LIQUIDITY_DEPTH_UNAVAILABLE')
    lp = min(1, 0.72 * (1 - lp_protection) + 0.28 * thin)
    if not (ev.lp_burn_verified or ev.lp_lock_verified):
        flags.append('LP_PROTECTION_UNVERIFIED')
    if thin > 0.5:
        flags.append('THIN_LIQUIDITY')
    buys = ev.slot0_buys
    collusion = 1 - min(1, ev.unique_funders_in_slot0 / buys) if buys else 0
    slot_ratio = min(1, buys / max(1, ev.total_early_buys))
    uniform = max(0, 1 - ev.buy_amount_cv / 0.35)
    bundler = min(1, 0.35 * slot_ratio * (0.3 + 0.7 * collusion) + 0.25 * min(1, ev.jito_bundle_count / 3) * (0.2 + 0.8 * collusion) + 0.2 * uniform + 0.2 * min(1, ev.wash_roundtrip_ratio * 2))
    if collusion > 0.6 and buys >= 3:
        flags.append('SLOT0_FUNDER_CLUSTER')
    if ev.jito_bundle_count:
        flags.append('JITO_BUNDLE_ACTIVITY')
    z = -3.2 + 3.25 * auth + 2.4 * conc + 3.15 * lp + 2.5 * bundler
    rug = 1 / (1 + math.exp(-max(-35, min(35, z))))
    momentum = (1 - rug) * (0.5 + 0.5 * min(1, ev.total_early_buys / 35))
    risk = 'CRITICAL' if rug >= 0.7 else 'HIGH' if rug >= 0.45 else 'MEDIUM' if rug >= 0.2 else 'LOW'
    return {'mint': ev.mint, 'block_age': age, 'in_window': True, 'rug_probability': round(rug, 4), 'momentum_probability': round(momentum, 4), 'risk_level': risk, 'flags': flags, 'components': {'authority': round(auth, 4), 'concentration': round(conc, 4), 'gini': round(gini, 4), 'top1_share': round(top1, 4), 'top10_share': round(top10, 4), 'lp_risk': round(lp, 4), 'bundler_risk': round(bundler, 4), 'lp_protection_verified': bool(ev.lp_burn_verified or ev.lp_lock_verified)}, 'calibration': 'heuristic; not trained on labeled market data'}

def main():
    if '--selftest' in sys.argv:
        import unittest
        from test_spl_rug_radar import Tests
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Tests)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    raw = json.load(sys.stdin)
    print(json.dumps(score_launch(raw), separators=(',', ':')))
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
