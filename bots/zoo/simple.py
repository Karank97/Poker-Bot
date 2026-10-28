"""Degenerate reference bots: what a weak field looks like."""
import numpy as np

from core.bot import Bot, call_or_check, check_or_fold
from core.state import FOLD, Action


class _Seeded(Bot):
    def __init__(self, name=None, seed=0):
        self.name = name or self.name
        self.rng = np.random.default_rng(seed)


def _raise_to(state, amount):
    lg = state.legal
    return Action(lg.raise_kind, int(max(lg.min_raise_to, min(lg.max_raise_to, amount))))


class FoldBot(_Seeded):
    """Checks when free, folds otherwise."""
    name = "fold"

    def act(self, state):
        return check_or_fold(state)


class CallBot(_Seeded):
    """Always calls / checks."""
    name = "call"

    def act(self, state):
        return call_or_check(state)


class RaiseBot(_Seeded):
    """Always min-raises (or bets the pot postflop), calls when it cannot raise."""
    name = "raise"

    def act(self, state):
        if state.legal.can_raise:
            target = state.legal.min_raise_to if state.street == "preflop" else state.my_street_bet + state.pot
            return _raise_to(state, target)
        return call_or_check(state)


class AllInBot(_Seeded):
    """Shoves every hand."""
    name = "allin"

    def act(self, state):
        if state.legal.can_raise:
            return _raise_to(state, state.legal.max_raise_to)
        return call_or_check(state)


class RandomBot(_Seeded):
    """Uniform over fold / call / raise (random size)."""
    name = "random"

    def act(self, state):
        lg = state.legal
        r = self.rng.random()
        if r < 0.33:
            return Action(FOLD) if not lg.can_check else check_or_fold(state)
        if r < 0.66 or not lg.can_raise:
            return call_or_check(state)
        return _raise_to(state, self.rng.integers(lg.min_raise_to, lg.max_raise_to + 1))
