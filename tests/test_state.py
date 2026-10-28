from core.state import (CALL, RAISE, Action, GameState, HistoryEntry, Legal,
                        position_names, seat_positions)


def test_position_names():
    assert position_names(8) == ["UTG", "UTG1", "MP", "HJ", "CO", "BTN", "SB", "BB"]
    assert position_names(3) == ["BTN", "SB", "BB"]
    assert position_names(2) == ["SB", "BB"]


def test_seat_positions():
    p = seat_positions(list(range(8)), 5)
    assert p[5] == "BTN" and p[6] == "SB" and p[7] == "BB" and p[0] == "UTG"
    hu = seat_positions(["a", "b"], "b")
    assert hu == {"b": "SB", "a": "BB"}


def test_state_helpers():
    s = GameState(my_seat=0, pot=150, to_call=100, my_stack=10000,
                  stacks={0: 10000, 1: 4000, 2: 9900}, live=[0, 1, 2],
                  positions=seat_positions([0, 1, 2], 0))
    assert s.n_live_opponents == 2
    assert abs(s.pot_odds - 100 / 250) < 1e-9
    assert s.effective_stack == 9900
    assert s.position == "BTN"
    s.history = [HistoryEntry(1, "preflop", RAISE, 300), HistoryEntry(2, "preflop", CALL, 300)]
    assert s.preflop_facing() == "raise" and s.last_aggressor() == 1


def test_action_and_legal():
    assert str(Action("raise", 300)) == "RAISE 300"
    assert str(Action("check")) == "CHECK"
    lg = Legal(can_check=True, can_raise=True, min_raise_to=100, max_raise_to=10000, raise_kind="bet")
    assert lg.actions() == ["check", "bet", "fold"]


def test_street_contrib():
    s = GameState(history=[HistoryEntry(1, "preflop", "post", 100), HistoryEntry(2, "preflop", RAISE, 300),
                           HistoryEntry(1, "preflop", CALL, 200), HistoryEntry(2, "flop", "bet", 50)])
    assert s.street_contrib(1, "preflop") == 300 and s.street_contrib(2, "preflop") == 300
    assert s.street_contrib(2, "flop") == 50
