"""scorer.py – Real‑time SPL mint & DEX pool anomaly/rug‑pull radar

The module provides a single public function `score_event(event: dict) -> float`
that returns a probability in the range ``0.0`` – ``1.0``.

The input ``event`` dictionary must contain the following keys:
* ``mint_authority`` – ``str`` or ``None``
* ``freeze_authority`` – ``str`` or ``None``
* ``initial_supply`` – ``int`` (total token supply at launch)
* ``holder_balances`` – ``list[int]`` – balances of the top‑10 holders
* ``lp_locked`` – ``bool`` – whether the initial LP tokens are locked/burned
* ``tx_slots`` – ``list[int]`` – slot numbers of the mint‑creation and pool‑creation txs

The implementation is deliberately lightweight (<8 KB source) and has no
external dependencies.
"""

from __future__ import annotations
from typing import List, Dict

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _gini_coefficient(values: List[int]) -> float:
    """Return the Gini coefficient for a list of non‑negative numbers.
    The result is in the range ``0`` (perfect equality) to ``1`` (maximal
    inequality). Empty or single‑element lists return ``0``.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    cumulative = 0
    for i, val in enumerate(sorted_vals, 1):
        cumulative += i * val
    total = sum(sorted_vals)
    if total == 0:
        return 0.0
    gini = (2 * cumulative) / (n * total) - (n + 1) / n
    return max(0.0, min(gini, 1.0))


def _slot_bundle_score(slots: List[int]) -> float:
    """Detect multiple txs in the same slot.
    Returns a score in ``0..1`` where ``1`` means all txs share the same slot.
    """
    if not slots:
        return 0.0
    most_common = max((slots.count(s) for s in set(slots)), default=1)
    return most_common / len(slots)

# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def score_event(event: Dict) -> float:
    """Calculate a rug‑pull probability for a newly created SPL mint.

    Parameters
    ----------
    event: dict
        Must contain the keys described in the module docstring.

    Returns
    -------
    float
        Probability in the range ``0.0`` – ``1.0``.
    """
    # Extract fields with safe defaults
    mint_authority = event.get("mint_authority")
    freeze_authority = event.get("freeze_authority")
    holder_balances = event.get("holder_balances", [])
    lp_locked = event.get("lp_locked", False)
    tx_slots = event.get("tx_slots", [])

    # 1. Freeze authority – presence reduces risk
    freeze_score = 0.0 if freeze_authority else 0.5  # 0 if present, 0.5 if absent

    # 2. Mint authority renounced – higher risk
    mint_score = 0.5 if mint_authority is None else 0.0

    # 3. Gini coefficient of top‑10 holders – normalized to 0..1
    gini = _gini_coefficient(holder_balances)
    gini_score = gini  # already 0..1

    # 4. LP lock status – unlocked LP is risky
    lp_score = 0.5 if not lp_locked else 0.0

    # 5. Slot bundler detection – multiple txs in same slot
    bundle = _slot_bundle_score(tx_slots)
    bundle_score = bundle * 0.5  # weight up to 0.5

    # Weighted sum (weights sum to 1.0)
    probability = (
        0.20 * freeze_score +
        0.25 * mint_score +
        0.20 * gini_score +
        0.20 * lp_score +
        0.15 * bundle_score
    )

    # Clip to [0,1]
    return max(0.0, min(probability, 1.0))

# ---------------------------------------------------------------------------
# Simple CLI for manual testing
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json, sys
    if len(sys.argv) != 2:
        print("Usage: python scorer.py <event_json_file>")
        sys.exit(1)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        ev = json.load(f)
    print(f"Rug‑pull probability: {score_event(ev):.4f}")
