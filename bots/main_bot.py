"""Main bot: tight-aggressive, position aware, equity and pot-odds driven.

Preflop: position-keyed chart (core.preflop) with mixed frequencies, push/fold
when the effective stack is short, a pot-odds check against big shoves.
Postflop: anytime Monte Carlo equity vs the live opponents with their ranges
narrowed by preflop action, thresholds that tighten multiway, two bet sizes
plus a shove, bluffs only heads-up.

Exploit hooks: `self.adjust(state)` returns an Adjust with multipliers and
shifts; the base bot returns neutral values and the opponent model (Phase 5)
overrides it.
"""
from dataclasses import dataclass, field

import numpy as np

from core.bot import Bot
from core.cards import board_category, category, evaluate
from core.equity import equity_detailed
from core.preflop import (PUSHFOLD_BB, chart, hand_percentile, percentile_matrix,
                          pushfold, strong_percentile, strong_range_combos)
from core.state import CALL, CHECK, FOLD, Action


@dataclass
class Config:
    equity_budget: float = 0.006     # seconds of Monte Carlo per postflop decision
    equity_samples: int = 800        # cap on samples
    open_bb: float = 2.5             # open size, +1bb per limper
    three_bet_ip: float = 3.0        # 3-bet to x the raise in position
    three_bet_oop: float = 3.8
    sizes: tuple = (0.5, 0.75)       # postflop bet sizes as a fraction of pot
    value_th: tuple = (0.62, 0.68, 0.74)   # value bet: HU, 3-way, 4+-way
    street_add: tuple = (0.0, 0.0, 0.0)  # value threshold bump on flop, turn, river
    strong_add: float = 0.14         # raise / big size above value_th + this
    cbet_bluff: float = 0.40         # HU flop c-bet frequency with air
    turn_bluff: float = 0.18
    river_bluff: float = 0.10
    call_margin: float = 0.04        # equity needed above pot odds to call a bet
    raise_margin: float = 0.06       # extra margin per raise beyond the first
    shove_spr: float = 1.6           # with value and SPR below this, get it in
    big_shove_frac: float = 0.35     # facing a raise >= this frac of our stack, use jam logic
    jam_range: tuple = (0.12, 0.04, 0.02)  # assumed shover range by preflop raise count 1 / 2 / 3+
    jam_call_margin: float = 0.03
    value_vs_call: float = 0.52      # value-bet / raise only with this equity vs calling range
    bet_air_share: tuple = (0.30, 0.20, 0.10)   # weight of "no improvement" combos for a bettor (flop/turn/river)
    raise_air_share: tuple = (0.12, 0.10, 0.06)  # ...for a raiser, who also needs two pair+ class hands


@dataclass
class Adjust:
    """Exploit knobs. Neutral = no opponent information."""
    bluff_mult: float = 1.0      # scales all bluff frequencies
    value_shift: float = 0.0     # subtract from value thresholds (thinner value)
    call_shift: float = 0.0      # subtract from the equity needed to call
    steal_mult: float = 1.0      # widens unopened late-position opens
    jam_call_mult: float = 1.0   # scales the assumed range of a big preflop raiser
    fold_to_aggr: float = 0.0    # add to call margin vs this bettor (respect nits)
    opp_range: float = 1.0       # scales assumed opponent range widths
    bettor_bluff: float = 1.0    # scales how often a bettor holds nothing (maniacs > 1)
    caller_air: float = 1.0      # scales how often a caller holds nothing (stations > 1)
    jam_width: float = 0.0       # if > 0: the shover's range width, from the opponent model
    trap: float = 0.0            # chance to check a strong hand to an aggressive opponent
    range_widths: dict = field(default_factory=dict)  # seat -> (raise width, call width)


class MainBot(Bot):
    name = "main"

    def __init__(self, name=None, seed=0, config=None):
        self.name = name or self.name
        self.rng = np.random.default_rng(seed)
        self.cfg = config or Config()
        self.pct = percentile_matrix()
        self.last = {}               # debug info from the last decision

    # ------------------------------------------------------------ hooks
    def adjust(self, state) -> Adjust:
        return Adjust()

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _raise_to(state, amount) -> Action:
        lg = state.legal
        if not lg.can_raise:
            return MainBot._call(state)
        return Action(lg.raise_kind, int(max(lg.min_raise_to, min(lg.max_raise_to, amount))))

    @staticmethod
    def _call(state) -> Action:
        if state.legal.call_amount > 0:
            return Action(CALL, state.legal.call_amount)
        return Action(CHECK) if state.legal.can_check else Action(FOLD)

    @staticmethod
    def _passive(state) -> Action:
        return Action(CHECK) if state.legal.can_check else Action(FOLD)

    def _pot_bet(self, state, frac) -> Action:
        pot_after_call = state.pot + state.to_call
        return self._raise_to(state, state.my_street_bet + state.to_call + frac * pot_after_call)

    def _sample(self, probs: dict) -> str:
        r, acc = self.rng.random(), 0.0
        for k, p in probs.items():
            acc += p
            if r < acc:
                return k
        return "fold"

    # ------------------------------------------------------------ act
    def act(self, state) -> Action:
        adj = self.adjust(state)
        if state.street == "preflop":
            return self.preflop(state, adj)
        return self.postflop(state, adj)

    # ------------------------------------------------------------ preflop
    def preflop(self, state, adj) -> Action:
        cfg, bb = self.cfg, state.big_blind
        eff_bb = state.effective_stack / bb
        facing = state.preflop_facing()
        n_players = len(state.positions) or 8
        pos = state.position

        # facing a shove or a huge raise: pot-odds driven jam-call logic
        my_total = state.my_stack + state.my_street_bet
        if facing != "unopened" and state.to_call >= cfg.big_shove_frac * my_total:
            return self._call_big_raise(state, adj)

        if eff_bb <= PUSHFOLD_BB:
            if facing == "unopened":
                probs = pushfold(state.hole, pos, eff_bb, "unopened",
                                 n_behind=max(1, state.players_behind()))
                if self.rng.random() < probs["jam"] * min(1.0, adj.steal_mult):
                    return self._raise_to(state, state.legal.max_raise_to)
                return self._passive(state)
            probs = pushfold(state.hole, pos, eff_bb, "jam", pot_odds=state.pot_odds)
            if self.rng.random() < probs["call"]:
                return self._raise_to(state, state.legal.max_raise_to)
            return self._passive(state)

        probs = chart(state.hole, pos, facing, n_players, state.limpers())
        if facing == "unopened" and adj.steal_mult != 1.0 and pos in ("CO", "BTN", "SB"):
            # widen steals: re-evaluate with a wider effective cutoff
            p = hand_percentile(state.hole)
            if probs["raise"] < 1 and p <= 0.42 * adj.steal_mult:
                probs = {"raise": 1.0, "call": 0.0, "fold": 0.0}
        choice = self._sample(probs)
        if choice == "raise":
            if facing == "unopened":
                return self._raise_to(state, (cfg.open_bb + state.limpers()) * bb)
            ip = pos in ("BTN", "CO") or (pos == "HJ" and state.players_behind() <= 2)
            mult = cfg.three_bet_ip if ip else cfg.three_bet_oop
            target = state.my_street_bet + state.to_call
            target = target * (mult if facing == "raise" else 2.3)
            if target >= 0.4 * my_total:
                target = state.legal.max_raise_to
            return self._raise_to(state, target)
        if choice == "call":
            return self._call(state)
        return self._passive(state)

    def _call_big_raise(self, state, adj) -> Action:
        """Facing a shove or a raise that commits us: equity vs the raiser's
        assumed range against the price."""
        cfg = self.cfg
        n_raises = sum(1 for h in state.history
                       if h.street == "preflop" and h.kind in ("raise", "bet") and h.seat != state.my_seat)
        width = cfg.jam_range[min(n_raises, 3) - 1] * adj.jam_call_mult
        if adj.jam_width > 0:
            width = adj.jam_width
        # opponents already in for a big amount (several shovers = several hands to beat)
        big = 0.5 * (state.my_street_bet + state.to_call)
        n_in = max(1, sum(1 for s in state.live if s != state.my_seat and state.street_contrib(s) >= big))
        if width >= 0.99:
            eq, _ = equity_detailed(state.hole, (), n_in, samples=300, time_budget=0.003, rng=self.rng)
        else:
            eq, _ = equity_detailed(state.hole, (), n_in, samples=300, time_budget=0.003, rng=self.rng,
                                    opp_range=strong_range_combos(round(min(1.0, width), 3)))
        # calling also risks the rest of our stack if the raiser is not all-in: price as all-in
        risk = min(state.my_stack, state.effective_stack - state.my_street_bet)
        po = risk / (state.pot + risk)
        self.last = {"eq": eq, "po": po, "width": width, "n": n_in}
        if eq >= po + cfg.jam_call_margin:
            if eq >= 0.6 and state.legal.can_raise:
                return self._raise_to(state, state.legal.max_raise_to)
            return self._call(state)
        return self._passive(state)

    # ------------------------------------------------------------ postflop
    def _range_width(self, state, seat, adj) -> float:
        """Assumed preflop range width for an opponent from their preflop action."""
        acts = [h.kind for h in state.history if h.seat == seat and h.street == "preflop"]
        known = adj.range_widths.get(seat)
        if "raise" in acts:
            w = known[0] if known else 0.18
        elif "call" in acts:
            w = known[1] if known else (0.35 if state.preflop_facing() != "unopened" else 0.55)
        else:
            w = 0.75   # blind that checked
        return min(1.0, w * adj.opp_range)

    def _weight_fn(self, state, opps, adj, level, role="bettor"):
        """Keep-probability for an opponent hand: preflop range width times a
        board-aware penalty for hands that do not improve on the board.
        level 0: no postflop info; 1: they bet (or would call a bet);
        2+: they raised (or would continue vs a raise)."""
        width = max(self._range_width(state, s, adj) for s in opps) if opps else 1.0
        if width >= 0.99 and level == 0:
            return None
        pct, board = self.pct, list(state.board)
        bcat = board_category(board)
        need = bcat + (1 if level <= 1 else 2)
        si = ("flop", "turn", "river").index(state.street)
        air = (self.cfg.bet_air_share if level <= 1 else self.cfg.raise_air_share)[si]
        air *= adj.bettor_bluff if role == "bettor" else adj.caller_air
        air = min(1.0, air)

        def weight(a, b):
            x = 1.0 if pct[a][b] <= width else 0.08
            if level and category(evaluate([a, b] + board)) < need:
                x *= air
            return x
        return weight

    def equity(self, state, adj, level=None, budget_frac=1.0, role="bettor"):
        """Equity vs the live opponents. level=None: their actual aggression
        this street; an int: the range that would continue at that level."""
        opps = [s for s in state.live if s != state.my_seat]
        if level is None:
            level = state.raises_this_street() if state.to_call > 0 else 0
        weight = self._weight_fn(state, opps, adj, level, role)
        eq, n = equity_detailed(state.hole, state.board, max(1, len(opps)),
                                samples=self.cfg.equity_samples,
                                time_budget=self.cfg.equity_budget * budget_frac,
                                rng=self.rng, opp_weight=weight)
        return eq, n

    def postflop(self, state, adj) -> Action:
        cfg = self.cfg
        n = max(1, state.n_live_opponents)
        eq, samples = self.equity(state, adj, budget_frac=0.5)
        street_i = ("flop", "turn", "river").index(state.street)
        value_th = cfg.value_th[min(n, 3) - 1] + cfg.street_add[street_i] - adj.value_shift
        strong_th = value_th + cfg.strong_add
        spr = state.spr
        self.last = {"eq": eq, "samples": samples, "po": state.pot_odds, "spr": spr, "n": n}

        if state.to_call == 0:
            if eq >= value_th:
                # value only if we beat the range that calls
                eq_call, _ = self.equity(state, adj, level=1, budget_frac=0.5, role="caller")
                self.last["eq_call"] = eq_call
                if eq_call < cfg.value_vs_call + cfg.street_add[street_i] - adj.value_shift:
                    return self._passive(state)
                if spr < cfg.shove_spr and eq >= strong_th - 0.05:
                    return self._raise_to(state, state.legal.max_raise_to)
                if eq >= strong_th and n == 1 and self.rng.random() < max(0.12, adj.trap):
                    return self._passive(state)   # occasional slowplay heads-up
                size = cfg.sizes[1] if eq >= strong_th or self.rng.random() < 0.35 else cfg.sizes[0]
                return self._pot_bet(state, size)
            if n == 1:
                freq = self._bluff_freq(state) * adj.bluff_mult
                if eq < value_th - 0.12 and self.rng.random() < freq:
                    return self._pot_bet(state, cfg.sizes[0])
            return self._passive(state)

        raises = state.raises_this_street()
        need = state.pot_odds + cfg.call_margin + cfg.raise_margin * max(0, raises - 1)
        need += adj.fold_to_aggr - adj.call_shift
        if n > 1:
            need += 0.02 * (n - 1)
        if eq >= strong_th and self.rng.random() < 0.75 and \
                self.equity(state, adj, level=raises + 1, budget_frac=0.5, role="caller")[0] >= cfg.value_vs_call:
            if spr < cfg.shove_spr * 2 or raises >= 2:
                return self._raise_to(state, state.legal.max_raise_to)
            return self._pot_bet(state, cfg.sizes[1])
        if eq >= need:
            return self._call(state)
        return self._passive(state)

    def _bluff_freq(self, state) -> float:
        me = state.my_seat
        preflop_aggressor = state.last_aggressor("preflop") == me
        if state.street == "flop":
            return self.cfg.cbet_bluff if preflop_aggressor else self.cfg.cbet_bluff * 0.3
        if state.street == "turn":
            return self.cfg.turn_bluff
        return self.cfg.river_bluff
