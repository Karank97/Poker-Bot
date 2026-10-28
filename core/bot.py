"""Minimal bot interface shared by the simulator, the zoo and the adapter."""
from .state import CALL, CHECK, FOLD, Action, GameState, HandResult


class Bot:
    name = "bot"

    def act(self, state: GameState) -> Action:
        """Return one Action. Must be fast and must not raise (the wrappers
        catch it anyway)."""
        return check_or_fold(state)

    def hand_over(self, result: HandResult) -> None:
        """Called once per hand with the full public history and showdowns."""


def check_or_fold(state: GameState) -> Action:
    return Action(CHECK) if state.legal.can_check else Action(FOLD)


def call_or_check(state: GameState) -> Action:
    if state.legal.call_amount > 0:
        return Action(CALL, state.legal.call_amount)
    return Action(CHECK) if state.legal.can_check else Action(FOLD)
