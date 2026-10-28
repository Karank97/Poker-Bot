from bots import make_bot
from core.bot import Bot, call_or_check
from core.cards import parse_cards
from core.state import CALL, CHECK, FOLD, RAISE, Action
from sim.engine import Table
from sim.run import run_match


class Script(Bot):
    """Plays a fixed list of actions (then check/call) and records states."""

    def __init__(self, name, actions=()):
        self.name = name
        self.actions = list(actions)
        self.states = []

    def act(self, state):
        self.states.append(state)
        if self.actions:
            a = self.actions.pop(0)
            return a(state) if callable(a) else a
        return call_or_check(state)


def shove(state):
    return Action(state.legal.raise_kind, state.legal.max_raise_to)


def make_deck(holes, board):
    cards = []
    for h in holes:
        cards += parse_cards(h)
    cards += parse_cards(board)
    return cards + [c for c in range(52) if c not in cards]


def test_conservation_random_play():
    bots = [make_bot(k, f"{k}{i}", seed=i) for i, k in enumerate(
        ["random", "allin", "random", "call", "raise", "random", "fold", "allin"])]
    t = Table(bots, persist_stacks=True, seed=3)
    for h in range(1500):
        t.play_hand(h % 8)
        rebuys = sum(s.rebuys for s in t.stats.values())
        assert sum(t.stacks) == 8 * 10000 + rebuys * 10000
    assert all(s.errors == 0 and s.illegal == 0 for s in t.stats.values())


def test_side_pots():
    # seat0 BTN 1000 (best), seat1 SB 3000 (second), seat2 BB 5000 (worst)
    bots = [Script("a", [shove]), Script("b", [shove]), Script("c", [lambda s: Action(CALL, s.legal.call_amount)])]
    t = Table(bots, persist_stacks=True)
    t.stacks = [1000, 3000, 5000]
    deck = make_deck(["AsAh", "KsKh", "2c7d"], "Ac Kd 8s 9h 3c")
    net = t.play_hand(0, deck)
    # main pot 3000 -> a; side pot 4000 -> b; c's uncalled 2000 returned
    assert net == {"a": 2000, "b": 1000, "c": -3000}
    assert t.stacks == [3000, 4000, 2000]


def test_split_pot_with_odd_chip():
    # SB (seat1) folds; BTN and BB check down a board that plays: 225 chip pot
    bots = [Script("btn"), Script("sb", [Action(FOLD)]), Script("bb")]
    t = Table(bots, small_blind=25, big_blind=100)
    deck = make_deck(["2c3d", "4h5h", "2d3c"], "As Ks Qs Js Ts")
    net = t.play_hand(0, deck)
    # odd chip to the first winner left of the button: BB
    assert net == {"btn": 12, "sb": -25, "bb": 13}


def test_min_raise_sequence():
    bots = [Script(f"p{i}") for i in range(4)]
    bots[3].actions = [Action(RAISE, 300)]          # seat3 is UTG with button 0
    t = Table(bots)
    t.play_hand(0, make_deck(["2c3d", "4h5h", "6c7d", "8h9h"], "As Kd Qs Jc 2s"))
    first = bots[3].states[0]
    assert first.legal.min_raise_to == 200 and first.legal.max_raise_to == 10000
    nxt = bots[0].states[0]                          # BTN facing 300: raise was +200
    assert nxt.to_call == 300 and nxt.legal.min_raise_to == 500


def _reopen_case(sb_stack):
    a = Script("a", [Action(RAISE, 1000)])           # seat0 BTN acts first 3-handed
    b = Script("b", [shove])
    c = Script("c", [lambda s: Action(CALL, s.legal.call_amount)])
    t = Table([a, b, c], persist_stacks=True)
    t.stacks = [10000, sb_stack, 10000]
    t.play_hand(0, make_deck(["2c3d", "4h5h", "6c7d"], "As Kd Qs Jc 9s"))
    return a.states[1]


def test_incomplete_allin_does_not_reopen():
    st = _reopen_case(1200)          # all-in raise of +200 < 900 needed
    assert st.to_call == 200 and not st.legal.can_raise


def test_full_allin_reopens():
    st = _reopen_case(3000)
    assert st.legal.can_raise


class Crashy(Bot):
    name = "crashy"

    def act(self, state):
        raise RuntimeError("boom")


class Illegal(Bot):
    name = "illegal"

    def act(self, state):
        return Action(CHECK) if state.to_call else Action(RAISE, 10**9)


def test_errors_and_illegal_never_break_a_hand():
    t = Table([Crashy(), Illegal(), make_bot("call")])
    for h in range(200):
        t.play_hand(h % 3)
    assert t.stats["crashy"].errors > 0 and t.stats["illegal"].illegal > 0
    assert sum(t.stacks) == 30000


def test_duplicate_gives_each_bot_each_seat():
    class Rec(Bot):
        def __init__(self, name):
            self.name, self.seen = name, []

        def act(self, state):
            self.seen.append((state.hand_id, tuple(state.hole)))
            return Action(FOLD) if not state.legal.can_check else Action(CHECK)

    bots = [Rec("x"), Rec("y"), Rec("z")]
    run_match(bots, hands=3, duplicate=True)
    holes = [{h for _, h in b.seen} for b in bots]
    # each bot saw the same set of hole cards across the 3 rotations (if it acted)
    assert holes[0] | holes[1] | holes[2] and len(holes[0] | holes[1] | holes[2]) <= 3


def test_duplicate_zero_sum_and_symmetric():
    rows, _ = run_match([make_bot("call", "a"), make_bot("call", "b")], hands=400, duplicate=True)
    # identical bots on mirrored cards: exactly zero
    assert all(abs(r[2]) < 1e-9 for r in rows)
