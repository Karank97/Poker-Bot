"""Preflop: cached equity table (169 classes x 1..7 random opponents, plus
heads-up equity vs a top-15% range) and an
8-handed, position-keyed open / call / 3-bet / fold chart plus push/fold logic.

Build the table offline once:  python -m core.preflop --samples 20000
The JSON lives in core/data/preflop_equity.json and loads in milliseconds.

All ranges are "top X% of combos" by `hand_percentile`, so tuning a position is
one number. Decisions return action probabilities so callers can mix with a
seeded RNG; hands near a range boundary get mixed frequencies.
"""
import argparse
import json
import os
from functools import lru_cache

import numpy as np

from .cards import ALL_CLASSES, class_all_combos, class_combos, class_to_cards, hand_class
from .equity import equity

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "preflop_equity.json")
MAX_OPPS = 7
VS_RANGE_PCT = 0.15   # the last table column: HU equity vs this top range

POSITIONS = ("UTG", "UTG1", "MP", "HJ", "CO", "BTN", "SB", "BB")

# Fraction of combos to open-raise first in (limpers ahead count as unopened).
OPEN_PCT = {"UTG": 0.13, "UTG1": 0.15, "MP": 0.18, "HJ": 0.22,
            "CO": 0.28, "BTN": 0.42, "SB": 0.34, "BB": 0.30}
# Facing one raise: 3-bet the top THREEBET_PCT, call down to CALL_PCT.
THREEBET_PCT = {"UTG": 0.030, "UTG1": 0.030, "MP": 0.035, "HJ": 0.035,
                "CO": 0.045, "BTN": 0.050, "SB": 0.045, "BB": 0.045}
CALL_PCT = {"UTG": 0.08, "UTG1": 0.08, "MP": 0.09, "HJ": 0.10,
            "CO": 0.12, "BTN": 0.15, "SB": 0.10, "BB": 0.20}
# Facing a 3-bet or more: 4-bet/jam top FOURBET_PCT, call down to CALL3_PCT.
FOURBET_PCT = 0.022   # ~QQ+, AKs
CALL3_PCT = 0.045     # ~TT+, AQs+, AK
MIX_BAND = 0.015      # width of the mixed-frequency ramp at each boundary

PUSHFOLD_BB = 15      # at or below this effective stack, play push/fold


# ------------------------------------------------------------ equity table

def _class_row(args):
    cls, samples, seed = args
    rng = np.random.default_rng(seed)
    hole = class_to_cards(cls)
    return cls, [round(equity(hole, (), n, samples=samples, rng=rng), 4)
                 for n in range(1, MAX_OPPS + 1)]


def _vs_range_cell(args):
    cls, samples, seed, combos = args
    rng = np.random.default_rng(seed)
    return cls, round(equity(class_to_cards(cls), (), 1, samples=samples, rng=rng,
                             opp_range=combos), 4)


def compute_table(samples=4000, processes=None, seed=7):
    """Rows: [eq vs 1..7 random opponents] + [HU eq vs top VS_RANGE_PCT]."""
    from multiprocessing import Pool
    jobs = [(cls, samples, seed + i) for i, cls in enumerate(ALL_CLASSES)]
    with Pool(processes) as pool:
        table = dict(pool.map(_class_row, jobs))
        pct = _equity_percentiles(table)
        combos = [c for cls, p in pct.items() if p <= VS_RANGE_PCT for c in class_all_combos(cls)]
        jobs = [(cls, samples, seed + 1000 + i, combos) for i, cls in enumerate(ALL_CLASSES)]
        for cls, e in pool.map(_vs_range_cell, jobs):
            table[cls].append(e)
    return table


def save_table(table, path=DATA_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(table, f, indent=0, sort_keys=True)


@lru_cache(maxsize=1)
def load_table(path=DATA_PATH):
    """Load the cached table, computing a quick (noisier) one if missing."""
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    table = compute_table(samples=1500)
    save_table(table, path)
    return table


def preflop_equity(cls_or_cards, n_opps: int) -> float:
    n = max(1, min(MAX_OPPS, n_opps))
    return load_table()[_cls(cls_or_cards)][n - 1]


# ------------------------------------------------------------ hand ranking

def _to_percentiles(order):
    total = sum(class_combos(c) for c in ALL_CLASSES)
    out, seen = {}, 0
    for c in order:
        # midpoint of the class's combo span: AA ~ 0.002, 72o ~ 0.995
        out[c] = (seen + class_combos(c) / 2) / total
        seen += class_combos(c)
    return out


def _equity_percentiles(t):
    """Rank by the mean of the HU and 3-way equity ranks so suited and paired
    hands (which play well multiway) are not buried under offsuit broadways."""
    def rank_by(i):
        order = sorted(ALL_CLASSES, key=lambda c: -t[c][i])
        return {c: k for k, c in enumerate(order)}
    r1, r3 = rank_by(0), rank_by(2)
    return _to_percentiles(sorted(ALL_CLASSES, key=lambda c: (r1[c] + r3[c], -t[c][0])))


@lru_cache(maxsize=1)
def _percentiles():
    return _equity_percentiles(load_table())


@lru_cache(maxsize=1)
def _strong_percentiles():
    """Rank by HU equity vs a top-15% range: the right order for re-raising
    (AK and AQs move ahead of small pairs)."""
    t = load_table()
    return _to_percentiles(sorted(ALL_CLASSES, key=lambda c: -t[c][MAX_OPPS]))


def _cls(cls_or_cards):
    return cls_or_cards if isinstance(cls_or_cards, str) else hand_class(*cls_or_cards)


def hand_percentile(cls_or_cards) -> float:
    """0.0 = best hand, 1.0 = worst, by fraction of combos ahead of it.
    Use for opening ranges."""
    return _percentiles()[_cls(cls_or_cards)]


def strong_percentile(cls_or_cards) -> float:
    """Like hand_percentile but ranked vs a strong range. Use facing raises."""
    return _strong_percentiles()[_cls(cls_or_cards)]


@lru_cache(maxsize=1)
def percentile_matrix():
    """m[c1][c2] = hand_percentile of the two cards, for fast range weights."""
    pct = _percentiles()
    return [[pct[hand_class(a, b)] if a != b else 1.0 for b in range(52)] for a in range(52)]


@lru_cache(maxsize=64)
def strong_range_combos(pct: float) -> list:
    """All (c1, c2) combos in the top `pct` by the strong (vs-raise) ranking."""
    sp = _strong_percentiles()
    out = [c for cls, p in sp.items() if p <= max(pct, 0.005) for c in class_all_combos(cls)]
    return out


def top_range(pct: float) -> set[str]:
    return {c for c, p in _percentiles().items() if p <= pct}


def _ramp(p: float, cutoff: float, band: float = MIX_BAND) -> float:
    """1.0 well inside the range, 0.0 well outside, linear in between."""
    if band <= 0:
        return 1.0 if p <= cutoff else 0.0
    return float(min(1.0, max(0.0, (cutoff + band / 2 - p) / band)))


# ------------------------------------------------------------ chart

def chart(cls_or_cards, position: str, facing: str = "unopened",
          n_players: int = 8, limpers: int = 0) -> dict:
    """Preflop action probabilities {'raise': p, 'call': p, 'fold': p}.

    facing: 'unopened' (no raise yet; limpers allowed), 'raise' (one raise),
    '3bet' (two or more raises). Short-handed tables widen opens because the
    early positions are missing.
    """
    p = hand_percentile(cls_or_cards) if facing == "unopened" else strong_percentile(cls_or_cards)
    pos = position if position in OPEN_PCT else "MP"
    widen = 1.0 + 0.12 * max(0, 8 - n_players)
    if facing == "unopened":
        cut = min(0.9, OPEN_PCT[pos] * widen * (0.85 if limpers else 1.0))
        r = _ramp(p, cut)
        if pos == "BB":  # BB checks the rest for free
            return {"raise": r, "call": 1 - r, "fold": 0.0}
        if limpers and pos == "SB":  # complete cheaply with a wider range
            c = _ramp(p, cut * 1.8) - r
            return {"raise": r, "call": max(0.0, c), "fold": max(0.0, 1 - r - c)}
        return {"raise": r, "call": 0.0, "fold": 1 - r}
    if facing == "raise":
        r = _ramp(p, THREEBET_PCT[pos] * widen)
        c = max(0.0, _ramp(p, CALL_PCT[pos] * widen) - r)
        return {"raise": r, "call": c, "fold": max(0.0, 1 - r - c)}
    r = _ramp(p, FOURBET_PCT, MIX_BAND / 3)
    c = max(0.0, _ramp(p, CALL3_PCT, MIX_BAND / 3) - r)
    return {"raise": r, "call": c, "fold": max(0.0, 1 - r - c)}


# ------------------------------------------------------------ push / fold

def jam_pct(position: str, eff_bb: float, n_behind: int) -> float:
    """Range to open-jam with `eff_bb` big blinds and `n_behind` players left
    to act. Rough Nash-like shape: wider when short and when fewer can call."""
    eff_bb = max(1.0, eff_bb)
    behind = max(1, n_behind)
    base = 0.55 / behind ** 0.8           # ~0.55 HU (SB), ~0.11 with 7 behind
    pct = base * (10.0 / eff_bb) ** 0.6
    if position == "SB" and behind == 1:
        pct = max(pct, 0.45)
    return float(min(1.0, max(0.02, pct)))


def call_jam_pct(eff_bb: float, pot_odds: float, n_jammers: int = 1) -> float:
    """Range to call an all-in. Tighter for multiple jammers, wider when the
    price is good. pot_odds = to_call / (pot + to_call)."""
    eff_bb = max(1.0, eff_bb)
    pct = 0.06 * (15.0 / eff_bb) ** 0.7 * (1.0 + 2.0 * max(0.0, 0.40 - pot_odds))
    return float(min(0.6, max(0.025, pct / n_jammers)))


def pushfold(cls_or_cards, position: str, eff_bb: float, facing: str,
             n_behind: int = 1, pot_odds: float = 0.5, n_jammers: int = 1) -> dict:
    """{'jam': p, 'fold': p} when unopened, {'call': p, 'fold': p} facing a jam."""
    p = hand_percentile(cls_or_cards) if facing == "unopened" else strong_percentile(cls_or_cards)
    if facing == "unopened":
        j = _ramp(p, jam_pct(position, eff_bb, n_behind))
        return {"jam": j, "fold": 1 - j}
    c = _ramp(p, call_jam_pct(eff_bb, pot_odds, n_jammers))
    return {"call": c, "fold": 1 - c}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the preflop equity table")
    ap.add_argument("--samples", type=int, default=20000)
    ap.add_argument("--processes", type=int, default=None)
    a = ap.parse_args()
    save_table(compute_table(a.samples, a.processes))
    print("wrote", DATA_PATH)
