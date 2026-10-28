"""8-seat no-limit hold'em engine: blinds, min-raise / reopen rules, side pots,
split pots, optional stack persistence, and duplicate deals.

Bots see a GameState built fresh for each decision and get a HandResult when
the hand ends. Exceptions and illegal actions never stop a hand: an exception
becomes check/fold, an illegal action is mapped to the nearest legal one, and
both are counted per bot.
"""
import time
from dataclasses import dataclass, field

import numpy as np

from core.cards import evaluate
from core.state import (BET, CALL, CHECK, FOLD, RAISE, STREETS, Action,
                        GameState, HandResult, HistoryEntry, Legal, seat_positions)


@dataclass
class SeatStats:
    hands: int = 0
    errors: int = 0
    illegal: int = 0
    decisions: int = 0
    think_time: float = 0.0
    max_think: float = 0.0
    rebuys: int = 0
    last_error: str = ""


@dataclass
class _P:
    stack: int
    street_bet: int = 0
    committed: int = 0
    folded: bool = False
    hole: list = field(default_factory=list)

    @property
    def all_in(self):
        return self.stack == 0 and not self.folded


class Table:
    def __init__(self, bots, small_blind=50, big_blind=100, stack=10000,
                 persist_stacks=False, seed=0):
        if not 2 <= len(bots) <= 8:
            raise ValueError("2..8 bots")
        self.bots = list(bots)               # self.bots[seat]
        self.sb, self.bb = small_blind, big_blind
        self.start_stack = stack
        self.persist = persist_stacks
        self.stacks = [stack] * len(bots)
        self.stats = {b.name: SeatStats() for b in self.bots}
        self.rng = np.random.default_rng(seed)
        self.hand_id = 0

    # ------------------------------------------------------------ public
    def seat_bots(self, bots):
        """Re-seat (for duplicate rotation). Stacks reset."""
        self.bots = list(bots)
        self.stacks = [self.start_stack] * len(bots)
        for b in bots:
            self.stats.setdefault(b.name, SeatStats())

    def play_hand(self, button, deck=None) -> dict:
        """Play one hand. Returns {bot name: net chips}."""
        n = len(self.bots)
        if deck is None:
            deck = self.rng.permutation(52).tolist()
        if not self.persist:
            self.stacks = [self.start_stack] * n
        for s in range(n):
            if self.stacks[s] <= 0:
                self.stacks[s] = self.start_stack
                self.stats[self.bots[s].name].rebuys += 1
        self.hand_id += 1
        seats = list(range(n))
        P = [_P(self.stacks[s], hole=deck[2 * s:2 * s + 2]) for s in seats]
        board_cards = deck[2 * n:2 * n + 5]
        start = {s: P[s].stack for s in seats}
        positions = seat_positions(seats, button)
        names = {s: self.bots[s].name for s in seats}
        history = []
        for b in self.bots:
            self.stats[b.name].hands += 1

        sb_seat = button if n == 2 else (button + 1) % n
        bb_seat = (sb_seat + 1) % n
        self._post(P[sb_seat], self.sb)
        self._post(P[bb_seat], self.bb)
        history.append(HistoryEntry(sb_seat, "preflop", "post", P[sb_seat].street_bet))
        history.append(HistoryEntry(bb_seat, "preflop", "post", P[bb_seat].street_bet))

        board = []
        for street in STREETS:
            if street == "flop":
                board = board_cards[:3]
            elif street == "turn":
                board = board_cards[:4]
            elif street == "river":
                board = board_cards[:5]
            if street == "preflop":
                first = (bb_seat + 1) % n
                current_bet = max(P[sb_seat].street_bet, P[bb_seat].street_bet)
            else:
                for p in P:
                    p.street_bet = 0
                first = (button + 1) % n
                current_bet = 0
            if self._live_count(P) > 1 and self._can_act_count(P) > 1 or (
                    street == "preflop" and self._needs_preflop_action(P, current_bet)):
                self._betting_round(P, street, first, current_bet, board, positions,
                                    names, history, button)
            if self._live_count(P) == 1:
                break
        board = board_cards[:5] if self._live_count(P) > 1 else board

        net, showdown = self._settle(P, board, button)
        for s in seats:
            self.stacks[s] = P[s].stack
        result = HandResult(self.hand_id, button, names, positions, list(board),
                            history, showdown, net, start)
        for s, b in enumerate(self.bots):
            try:
                b.hand_over(result)
            except Exception as e:  # noqa: BLE001 - a bot must never stop the table
                st = self.stats[b.name]
                st.errors += 1
                st.last_error = repr(e)
        return {names[s]: net[s] for s in seats}

    # ------------------------------------------------------------ betting
    @staticmethod
    def _post(p, amount):
        a = min(amount, p.stack)
        p.stack -= a
        p.street_bet += a
        p.committed += a

    @staticmethod
    def _live_count(P):
        return sum(1 for p in P if not p.folded)

    @staticmethod
    def _can_act_count(P):
        return sum(1 for p in P if not p.folded and p.stack > 0)

    @staticmethod
    def _needs_preflop_action(P, current_bet):
        # someone with chips still faces a bet (e.g. BB short all-in vs one caller)
        return any(not p.folded and p.stack > 0 and p.street_bet < current_bet for p in P)

    def _betting_round(self, P, street, first, current_bet, board, positions,
                       names, history, button):
        n = len(P)
        last_raise = self.bb
        active = {s for s in range(n) if not P[s].folded and P[s].stack > 0}
        needs = set(active)
        can_reraise = set(active)
        i = first
        while needs:
            s = i % n
            i += 1
            if s not in needs:
                continue
            p = P[s]
            if p.folded or p.stack == 0:
                needs.discard(s)
                continue
            if self._live_count(P) == 1:
                return
            to_call = min(current_bet - p.street_bet, p.stack)
            others = [q for q in range(n) if q != s and not P[q].folded and P[q].stack > 0]
            if not others and to_call <= 0:
                needs.discard(s)
                continue

            legal = Legal(can_fold=True, can_check=to_call <= 0, call_amount=max(0, to_call))
            max_to = p.street_bet + p.stack
            if p.stack > to_call and s in can_reraise and others:
                legal.can_raise = True
                legal.min_raise_to = min(current_bet + last_raise, max_to)
                legal.max_raise_to = max_to
                legal.raise_kind = BET if current_bet == 0 else RAISE
            state = GameState(
                hole=list(p.hole), board=list(board), street=street,
                pot=sum(q.committed for q in P), to_call=max(0, to_call),
                min_raise_to=legal.min_raise_to, max_raise_to=legal.max_raise_to,
                my_seat=s, my_stack=p.stack, my_street_bet=p.street_bet,
                big_blind=self.bb, small_blind=self.sb,
                stacks={q: P[q].stack for q in range(n)},
                positions=dict(positions), names=dict(names),
                live=[q for q in range(n) if not P[q].folded],
                history=list(history), legal=legal, hand_id=self.hand_id, button=button)
            action = self._ask(s, state)
            kind, amt = self._normalize(action, legal, self.bots[s].name)

            needs.discard(s)
            can_reraise.discard(s)
            if kind == FOLD:
                p.folded = True
                history.append(HistoryEntry(s, street, FOLD))
            elif kind == CHECK:
                history.append(HistoryEntry(s, street, CHECK))
            elif kind == CALL:
                self._post(p, amt)
                history.append(HistoryEntry(s, street, CALL, amt))
            else:
                add = amt - p.street_bet
                self._post(p, add)
                inc = amt - current_bet
                full = inc >= last_raise
                if full:
                    last_raise = inc
                current_bet = amt
                history.append(HistoryEntry(s, street, kind, amt))
                responders = {q for q in range(n) if q != s and not P[q].folded and P[q].stack > 0}
                needs |= responders
                if full:
                    can_reraise |= responders

    def _ask(self, s, state):
        bot = self.bots[s]
        st = self.stats[bot.name]
        t = time.perf_counter()
        try:
            action = bot.act(state)
        except Exception as e:  # noqa: BLE001
            st.errors += 1
            st.last_error = repr(e)
            action = None
        dt = time.perf_counter() - t
        st.decisions += 1
        st.think_time += dt
        st.max_think = max(st.max_think, dt)
        return action

    def _normalize(self, action, legal, name):
        """Map any action onto a legal (kind, amount). Count illegal ones."""
        st = self.stats[name]
        if not isinstance(action, Action):
            return (CHECK, 0) if legal.can_check else (FOLD, 0)
        k = action.kind
        if k in (BET, RAISE):
            if legal.can_raise:
                amt = int(action.amount)
                if not legal.min_raise_to <= amt <= legal.max_raise_to:
                    st.illegal += 1
                    amt = max(legal.min_raise_to, min(legal.max_raise_to, amt))
                return legal.raise_kind, amt
            st.illegal += 1
            k = CALL
        if k == CALL:
            if legal.call_amount > 0:
                return CALL, legal.call_amount
            if legal.can_check:
                return CHECK, 0
        if k == CHECK:
            if legal.can_check:
                return CHECK, 0
            st.illegal += 1
            return FOLD, 0
        if k == FOLD:
            return FOLD, 0
        st.illegal += 1
        return (CHECK, 0) if legal.can_check else (FOLD, 0)

    # ------------------------------------------------------------ showdown
    def _settle(self, P, board, button):
        n = len(P)
        contrib = [p.committed for p in P]
        start_total = sum(contrib)
        payout = [0] * n
        live = [s for s in range(n) if not P[s].folded]
        showdown = {}
        if len(live) == 1:
            payout[live[0]] = start_total
        else:
            scores = {s: evaluate(P[s].hole + board) for s in live}
            showdown = {s: list(P[s].hole) for s in live}
            order = [(button + 1 + k) % n for k in range(n)]  # odd chips: left of button first
            levels = sorted({contrib[s] for s in live})
            prev = 0
            for li, lvl in enumerate(levels):
                amount = sum(min(c, lvl) - min(c, prev) for c in contrib)
                if li == len(levels) - 1:
                    amount += sum(max(0, c - lvl) for c in contrib)  # dead money above top level
                elig = [s for s in live if contrib[s] >= lvl]
                best = max(scores[s] for s in elig)
                winners = [s for s in order if s in elig and scores[s] == best]
                share, odd = divmod(amount, len(winners))
                for k, w in enumerate(winners):
                    payout[w] += share + (1 if k < odd else 0)
                prev = lvl
        for s in range(n):
            P[s].stack += payout[s]
        net = {s: payout[s] - contrib[s] for s in range(n)}
        assert sum(net.values()) == 0, "chip conservation violated"
        return net, showdown
