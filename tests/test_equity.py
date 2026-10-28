import time

import numpy as np

from core.cards import parse_cards
from core.equity import equity, equity_detailed


def rng():
    return np.random.default_rng(0)


def test_aa_heads_up():
    assert abs(equity(parse_cards("AsAh"), (), 1, 4000, rng=rng()) - 0.852) < 0.02


def test_multiway_decreases():
    h = parse_cards("AsAh")
    e = [equity(h, (), n, 2000, rng=rng()) for n in (1, 3, 7)]
    assert e[0] > e[1] > e[2] > 0.3


def test_nuts_and_dead():
    # royal flush on board for us: 100%
    assert equity(parse_cards("AsKs"), parse_cards("QsJsTs2c3d"), 3, 200, rng=rng()) == 1.0
    # board plays a royal: everybody ties -> 1/(n+1)
    e = equity(parse_cards("2c3d"), parse_cards("AsKsQsJsTs"), 2, 200, rng=rng())
    assert abs(e - 1 / 3) < 1e-9


def test_known_river():
    # set vs one random hand on a dry river board is a big favourite
    e = equity(parse_cards("7h7d"), parse_cards("7c2s9dKhQc"), 1, 2000, rng=rng())
    assert e > 0.93


def test_time_budget_anytime():
    t = time.perf_counter()
    e, n = equity_detailed(parse_cards("AsAh"), (), 7, samples=10**7, time_budget=0.01, rng=rng())
    dt = time.perf_counter() - t
    assert dt < 0.03 and n >= 16 and 0 < e < 1


def test_opp_weight_tightens():
    h = parse_cards("AhJd")
    base = equity(h, (), 1, 3000, rng=rng())
    # opponents only keep hands with an ace or a pair -> worse for AJ
    from core.cards import rank_of
    w = lambda a, b: 1.0 if rank_of(a) == rank_of(b) or 12 in (rank_of(a), rank_of(b)) else 0.0
    tight = equity(h, (), 1, 3000, rng=rng(), opp_weight=w)
    assert tight < base - 0.05


def test_opp_range_exact():
    from core.cards import class_all_combos
    aa_vs_kk = equity(parse_cards("AsAh"), (), 1, 3000, rng=rng(), opp_range=class_all_combos("KK"))
    assert abs(aa_vs_kk - 0.82) < 0.02
