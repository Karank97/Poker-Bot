from core.preflop import (ALL_CLASSES, chart, hand_percentile, load_table,
                          preflop_equity, pushfold, strong_percentile, top_range)


def test_table_complete():
    t = load_table()
    assert set(t) == set(ALL_CLASSES)
    assert all(len(v) == 8 for v in t.values())
    assert strong_percentile("AKo") < strong_percentile("99")
    assert abs(preflop_equity("AA", 1) - 0.852) < 0.01
    assert preflop_equity("AA", 7) < preflop_equity("AA", 1)


def test_ranking():
    assert hand_percentile("AA") < hand_percentile("KK") < hand_percentile("T9s") < hand_percentile("72o")
    assert hand_percentile("AKs") < hand_percentile("AKo")
    assert {"AA", "KK", "QQ"} <= top_range(0.03)


def _sums(d):
    assert abs(sum(d.values()) - 1) < 1e-9 and all(v >= 0 for v in d.values())
    return d


def test_chart_positions():
    assert _sums(chart("AA", "UTG"))["raise"] == 1
    assert _sums(chart("72o", "BTN"))["fold"] == 1
    # Q9s: fold UTG, raise BTN
    assert chart("Q9s", "UTG")["fold"] == 1 and chart("Q9s", "BTN")["raise"] == 1
    # BB never folds unopened
    assert _sums(chart("72o", "BB"))["call"] == 1


def test_chart_facing():
    assert _sums(chart("AA", "CO", "raise"))["raise"] == 1
    assert _sums(chart("AA", "CO", "3bet"))["raise"] == 1
    assert _sums(chart("98s", "UTG", "3bet"))["fold"] == 1
    # a mid hand continues vs a raise in the BB but not from UTG
    assert chart("KQo", "BB", "raise")["call"] == 1 and chart("KQo", "UTG", "raise")["fold"] == 1
    # re-raise ranking puts AK ahead of small pairs
    assert chart("AKo", "BTN", "raise")["raise"] == 1 and chart("55", "BTN", "raise")["raise"] == 0


def test_pushfold():
    assert pushfold("AA", "UTG", 10, "unopened", n_behind=7)["jam"] == 1
    assert pushfold("72o", "UTG", 10, "unopened", n_behind=7)["fold"] == 1
    # SB vs BB short-stacked jams very wide
    assert pushfold("K7o", "SB", 8, "unopened", n_behind=1)["jam"] == 1
    assert pushfold("QQ", "BB", 10, "jam", pot_odds=0.45)["call"] == 1
    assert pushfold("94o", "BB", 10, "jam", pot_odds=0.45)["fold"] == 1
