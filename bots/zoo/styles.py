"""Style bots: the human-like part of the field.

Most are one Parametric class with different numbers. Ranges are "top X% of
combos" from core.preflop; postflop decisions use a cheap cached Monte Carlo
strength (equity vs the live opponents).
"""
from core.preflop import chart, hand_percentile, preflop_equity, strong_percentile
from core.state import FOLD, Action

from .common import Seeded, call, call_or_check, check_or_fold, pot_raise_to, raise_to, strength


class Parametric(Seeded):
    # preflop
    vpip = 0.25         # play this top fraction unopened (raise or limp)
    pfr = 0.18          # raise this top fraction unopened
    call_raise = 0.12   # call a raise with this top fraction (strong ranking)
    three_bet = 0.04    # re-raise with this top fraction
    open_bb = 3.0
    # postflop (equity vs live opponents)
    value_th = 0.62     # bet / raise for value above this
    call_slack = 0.0    # call when equity >= pot odds - slack
    call_floor = 0.0    # ...and equity >= floor
    bluff = 0.10        # bet as a bluff this often when checked to
    bet_frac = 0.66
    raise_th = 0.80

    def act(self, state):
        if state.street == "preflop":
            return self.preflop(state)
        return self.postflop(state)

    def preflop(self, state):
        facing = state.preflop_facing()
        if facing == "unopened":
            p = hand_percentile(state.hole)
            if p <= self.pfr:
                return raise_to(state, (self.open_bb + state.limpers()) * state.big_blind)
            if p <= self.vpip:
                return call_or_check(state)
            return check_or_fold(state)
        p = strong_percentile(state.hole)
        if facing == "raise":
            if p <= self.three_bet:
                return raise_to(state, state.my_street_bet + state.to_call * 3)
            if p <= self.call_raise:
                return call(state)
            return check_or_fold(state)
        if p <= self.three_bet / 2:
            return raise_to(state, state.legal.max_raise_to)
        if p <= self.call_raise / 3:
            return call(state)
        return check_or_fold(state)

    def postflop(self, state):
        hs = strength(self, state)
        if state.to_call == 0:
            if hs >= self.value_th or self.rng.random() < self.bluff:
                return pot_raise_to(state, self.bet_frac)
            return check_or_fold(state)
        if hs >= self.raise_th and self.rng.random() < 0.7:
            return pot_raise_to(state, self.bet_frac)
        if hs >= max(self.call_floor, state.pot_odds - self.call_slack):
            return call(state)
        return check_or_fold(state)


class Nit(Parametric):
    """Top 10% only, folds to aggression without a strong hand."""
    name = "nit"
    vpip, pfr, call_raise, three_bet = 0.10, 0.08, 0.05, 0.025
    value_th, call_floor, bluff, raise_th = 0.70, 0.60, 0.0, 0.85


class LoosePassive(Parametric):
    """Limps a lot, calls a lot, rarely bets."""
    name = "lp"
    vpip, pfr, call_raise, three_bet = 0.55, 0.04, 0.45, 0.01
    value_th, call_slack, call_floor, bluff, raise_th, bet_frac = 0.80, 0.10, 0.25, 0.02, 0.92, 0.5


class TAG(Parametric):
    name = "tag"
    vpip, pfr, call_raise, three_bet = 0.20, 0.17, 0.09, 0.04
    value_th, call_floor, bluff, raise_th = 0.62, 0.0, 0.12, 0.80


class LAG(Parametric):
    name = "lag"
    vpip, pfr, call_raise, three_bet = 0.38, 0.30, 0.20, 0.09
    value_th, call_slack, bluff, raise_th, bet_frac = 0.52, 0.05, 0.35, 0.72, 0.8


class FoldBot(Parametric):
    """FOLDBOT: folds almost everything; raises only premiums."""
    name = "foldbot"
    vpip, pfr, call_raise, three_bet = 0.025, 0.025, 0.02, 0.012
    value_th, call_floor, bluff, raise_th = 0.80, 0.80, 0.0, 0.90


class RiverRat(Parametric):
    """RIVERRAT: loose preflop, never folds postflop once in the pot."""
    name = "riverrat"
    vpip, pfr, call_raise, three_bet = 0.50, 0.08, 0.40, 0.02
    value_th, bluff, raise_th = 0.85, 0.0, 0.95

    def postflop(self, state):
        hs = strength(self, state)
        if hs >= self.raise_th:
            return pot_raise_to(state, 0.75)
        return call_or_check(state)


class Tilt(Parametric):
    """TILT: a LAG that gets looser and wilder after losses."""
    name = "tilt"
    base = dict(vpip=0.35, pfr=0.25, call_raise=0.18, three_bet=0.07, bluff=0.30,
                call_slack=0.05, value_th=0.55, bet_frac=0.8)

    def __init__(self, name=None, seed=0):
        super().__init__(name, seed)
        self.recent = []
        self._apply(0.0)

    def _apply(self, tilt):
        b = self.base
        self.vpip = min(0.9, b["vpip"] * (1 + 1.5 * tilt))
        self.pfr = min(0.8, b["pfr"] * (1 + 1.5 * tilt))
        self.call_raise = min(0.8, b["call_raise"] * (1 + 2 * tilt))
        self.three_bet = min(0.5, b["three_bet"] * (1 + 3 * tilt))
        self.bluff = min(0.8, b["bluff"] + 0.4 * tilt)
        self.call_slack = b["call_slack"] + 0.15 * tilt
        self.value_th = b["value_th"] - 0.1 * tilt
        self.bet_frac = b["bet_frac"] + 0.7 * tilt

    def hand_over(self, result):
        me = [s for s, n in result.names.items() if n == self.name]
        if not me:
            return
        self.recent = (self.recent + [result.net[me[0]]])[-30:]
        down_bb = -sum(self.recent) / 100.0
        self._apply(max(0.0, min(1.0, down_bb / 100.0)))   # full tilt at -100bb


class GTOLite(Seeded):
    """Tight-aggressive with balanced-looking frequencies: preflop chart,
    postflop value bets mixed with bluffs at the ratio the bet size implies,
    defends close to minimum defence frequency heads-up."""
    name = "gtolite"
    sizes = (0.33, 0.66)

    def act(self, state):
        if state.street == "preflop":
            return self._preflop(state)
        hs = strength(self, state, samples=64)
        n = max(1, state.n_live_opponents)
        adj = hs ** (1.0 / n) if n > 1 else hs   # rough per-opponent strength
        if state.to_call == 0:
            size = self.sizes[1] if adj > 0.8 else self.sizes[0]
            value_cut = 0.68
            bluff_ratio = size / (1 + 2 * size)   # bluffs per bet in a balanced range
            if adj >= value_cut:
                return pot_raise_to(state, size)
            if n == 1 and adj < 0.35 and self.rng.random() < bluff_ratio * 0.6:
                return pot_raise_to(state, size)
            return check_or_fold(state)
        mdf = 1 - state.pot_odds * 2 if state.pot_odds < 0.5 else 0.0
        if adj >= 0.85 and self.rng.random() < 0.6:
            return pot_raise_to(state, 0.75)
        if adj >= max(state.pot_odds, 1 - mdf - 0.05) or adj >= 0.6:
            return call(state)
        return check_or_fold(state)

    def _preflop(self, state):
        probs = chart(state.hole, state.position, state.preflop_facing(),
                      len(state.positions), state.limpers())
        r = self.rng.random()
        if r < probs["raise"]:
            facing = state.preflop_facing()
            if facing == "unopened":
                return raise_to(state, (2.5 + state.limpers()) * state.big_blind)
            return raise_to(state, state.my_street_bet + state.to_call * 3)
        if r < probs["raise"] + probs["call"]:
            return call_or_check(state)
        return check_or_fold(state)


class PotOdds(Seeded):
    """Basic equity bot: call when equity beats pot odds, bet when ahead."""
    name = "potodds"
    samples = 48

    def act(self, state):
        n = max(1, state.n_live_opponents)
        if state.street == "preflop":
            eq = preflop_equity(state.hole, n)
        else:
            eq = strength(self, state, self.samples)
        fair = 1.0 / (n + 1)
        if state.to_call == 0:
            if eq > fair * 1.6:
                return pot_raise_to(state, 0.6)
            return check_or_fold(state)
        if eq > fair * 2.2 and self.rng.random() < 0.5:
            return pot_raise_to(state, 0.6)
        if eq >= state.pot_odds:
            return call(state)
        return Action(FOLD)


class MonteCarlo(Seeded):
    """Larger-sample equity with a simple EV comparison of fold / call / raise,
    assuming opponents fold to a raise ~25% of the time per opponent."""
    name = "mc"
    samples = 200
    fold_eq = 0.25

    def act(self, state):
        n = max(1, state.n_live_opponents)
        if state.street == "preflop":
            if state.preflop_facing() == "unopened" and hand_percentile(state.hole) > 0.35:
                return check_or_fold(state)
            eq = preflop_equity(state.hole, n)
        else:
            eq = strength(self, state, self.samples)
        pot, tc = state.pot, state.to_call
        ev_call = eq * (pot + tc) - tc
        lg = state.legal
        best, best_ev = (call(state) if tc else check_or_fold(state)), (ev_call if tc else eq * pot)
        if lg.can_raise:
            for frac in (0.5, 1.0):
                a = pot_raise_to(state, frac)
                risk = a.amount - state.my_street_bet
                pf = self.fold_eq ** n
                ev = pf * pot + (1 - pf) * (eq * (pot + 2 * risk) - risk)
                if ev > best_ev:
                    best, best_ev = a, ev
        if tc and best_ev < 0 and ev_call < 0:
            return Action(FOLD)
        return best


class Bayes(Parametric):
    """Adaptive: Beta-smoothed per-opponent aggression and fold rates. Calls
    wider against aggressive opponents and bluffs more into folders."""
    name = "bayes"
    vpip, pfr, call_raise, three_bet = 0.22, 0.18, 0.10, 0.04
    value_th, bluff, raise_th = 0.60, 0.10, 0.80

    def __init__(self, name=None, seed=0):
        super().__init__(name, seed)
        self.agg = {}    # name -> [aggressive actions, total actions]  (prior 1/3)
        self.folds = {}  # name -> [folds facing a bet, times faced]   (prior 1/2)

    def hand_over(self, result):
        current = {}
        for h in result.history:
            nm = result.names.get(h.seat)
            if h.kind == "post" or nm == self.name:
                continue
            a = self.agg.setdefault(nm, [1.0, 3.0])
            a[0] += h.kind in ("bet", "raise")
            a[1] += 1
            if h.street != "preflop":
                facing = current.get(h.street, False)
                if facing:
                    f = self.folds.setdefault(nm, [1.0, 2.0])
                    f[0] += h.kind == "fold"
                    f[1] += 1
            if h.kind in ("bet", "raise"):
                current[h.street] = True

    def _rate(self, table, nm, prior):
        a = table.get(nm)
        return a[0] / a[1] if a else prior

    def postflop(self, state):
        opps = [state.names.get(s) for s in state.live if s != state.my_seat]
        agg = max((self._rate(self.agg, o, 1 / 3) for o in opps), default=1 / 3)
        fold = min((self._rate(self.folds, o, 0.5) for o in opps), default=0.5)
        self.call_slack = max(0.0, (agg - 0.33)) * 0.4
        self.bluff = 0.05 + max(0.0, fold - 0.5) * (0.8 if len(opps) == 1 else 0.2)
        return super().postflop(state)
