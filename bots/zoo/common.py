"""Helpers shared by zoo bots."""
import numpy as np

from core.bot import Bot, call_or_check, check_or_fold
from core.equity import equity
from core.state import CALL, Action


class Seeded(Bot):
    def __init__(self, name=None, seed=0):
        self.name = name or self.name
        self.rng = np.random.default_rng(seed)
        self._cache = None


def raise_to(state, amount) -> Action:
    lg = state.legal
    if not lg.can_raise:
        return call_or_check(state)
    return Action(lg.raise_kind, int(max(lg.min_raise_to, min(lg.max_raise_to, amount))))


def pot_raise_to(state, frac) -> Action:
    """Bet/raise to (call + frac * pot after calling)."""
    pot_after_call = state.pot + state.to_call
    return raise_to(state, state.my_street_bet + state.to_call + frac * pot_after_call)


def strength(bot, state, samples=48) -> float:
    """Monte Carlo equity vs the live opponents, cached per hand and street."""
    key = (state.hand_id, state.street, tuple(state.hole), len(state.live))
    if bot._cache and bot._cache[0] == key:
        return bot._cache[1]
    e = equity(state.hole, state.board, max(1, state.n_live_opponents), samples, rng=bot.rng)
    bot._cache = (key, e)
    return e


def call(state) -> Action:
    return Action(CALL, state.legal.call_amount) if state.legal.call_amount > 0 else check_or_fold(state)


__all__ = ["Seeded", "raise_to", "pot_raise_to", "strength", "call", "call_or_check", "check_or_fold"]
