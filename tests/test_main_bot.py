import time

from bots.main_bot import MainBot
from core.cards import parse_cards
from core.state import BET, CALL, CHECK, FOLD, RAISE, GameState, HistoryEntry, Legal, seat_positions


def make_state(hole, board=(), to_call=0, pot=150, button=5, me=0, live=None, history=None,
               stack=10000, street=None, my_bet=0, current_bet=None):
    seats = list(range(8))
    live = live if live is not None else seats
    street = street or {0: "preflop", 3: "flop", 4: "turn", 5: "river"}[len(board)]
    cb = current_bet if current_bet is not None else my_bet + to_call
    legal = Legal(can_check=to_call == 0, call_amount=to_call, can_raise=True,
                  min_raise_to=max(cb * 2, 100), max_raise_to=stack + my_bet,
                  raise_kind=RAISE if cb else BET)
    if street == "preflop" and history is None:
        history = [HistoryEntry(6, "preflop", "post", 50), HistoryEntry(7, "preflop", "post", 100)]
    return GameState(hole=parse_cards(hole), board=parse_cards(" ".join(board) if board else ""),
                     street=street, pot=pot, to_call=to_call, min_raise_to=legal.min_raise_to,
                     max_raise_to=legal.max_raise_to, my_seat=me, my_stack=stack, my_street_bet=my_bet,
                     stacks={s: stack for s in seats}, positions=seat_positions(seats, button),
                     live=live, history=history or [], legal=legal)


def bot():
    return MainBot(seed=1)


def test_preflop_premium_and_trash():
    b = bot()
    # seat 0 is UTG with button 5
    assert b.act(make_state("AsAh", to_call=100)).kind == RAISE
    assert b.act(make_state("7c2d", to_call=100)).kind == FOLD


def test_bb_checks_trash_for_free():
    b = bot()
    st = make_state("7c2d", to_call=0, me=7, my_bet=100, current_bet=100)
    st.history = [HistoryEntry(6, "preflop", "post", 50), HistoryEntry(7, "preflop", "post", 100),
                  HistoryEntry(0, "preflop", CALL, 100)]
    assert b.act(st).kind in (CHECK, RAISE)


def test_vs_shove():
    hist = [HistoryEntry(6, "preflop", "post", 50), HistoryEntry(7, "preflop", "post", 100),
            HistoryEntry(3, "preflop", RAISE, 10000)]
    aa = bot().act(make_state("AsAh", to_call=10000, pot=10150, me=5, history=hist))
    trash = bot().act(make_state("8c3d", to_call=10000, pot=10150, me=5, history=hist))
    assert aa.kind in (CALL, RAISE) and trash.kind == FOLD


def test_postflop_value_and_fold():
    hu = [0, 3]
    nuts = bot().act(make_state("AsKs", ["Qs", "Js", "Ts"], pot=600, live=hu))
    assert nuts.kind in (BET, CHECK)  # bets nearly always (rare slowplay)
    big_bet = bot().act(make_state("7c2d", ["As", "Kd", "Qh", "9c", "3s"], to_call=600, pot=1200, live=hu))
    assert big_bet.kind == FOLD


def test_tighter_multiway():
    # a marginal top pair bets heads-up more often than 6-way
    def bet_rate(live):
        b = bot()
        return sum(b.act(make_state("Kc9d", ["Ks", "7d", "2h"], pot=600, live=live)).kind == BET
                   for _ in range(60)) / 60
    assert bet_rate([0, 3]) > bet_rate([0, 1, 2, 3, 4, 5])


def test_decision_time():
    b = bot()
    st = make_state("Ah9h", ["Kh", "7h", "2c"], to_call=300, pot=900, live=[0, 1, 2, 3, 4, 5, 6, 7])
    t = time.perf_counter()
    for _ in range(20):
        b.act(st)
    assert (time.perf_counter() - t) / 20 < 0.02
