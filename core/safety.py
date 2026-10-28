"""Safety layer between any bot and the dealer.

SafeBot(bot) guarantees one legal action per decision, on time:
  - a deadline is handed to the bot (bot.deadline) so anytime Monte Carlo stops
    early; with hard_timeout the decision also runs on a worker thread and the
    fallback is returned if it is not back in time
  - any exception -> safest legal action (check, else fold)
  - every action is validated against the Legal set before it is returned
  - GC is paused during a decision and collected between hands
  - optional compact hand log (one line per hand) and per-decision debug line
    on stderr (never stdout: stdout may be the dealer protocol)
"""
import gc
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from .bot import Bot
from .cards import cards_str
from .state import BET, CALL, CHECK, FOLD, RAISE, Action, Legal


def safest(legal: Legal) -> Action:
    return Action(CHECK) if legal.can_check else Action(FOLD)


def validate(action, legal: Legal):
    """Return (legal_action, problem or None). Never raises."""
    try:
        if not isinstance(action, Action):
            return safest(legal), f"not an Action: {action!r}"
        k = action.kind
        if k == CHECK:
            if legal.can_check:
                return action, None
            return Action(FOLD), "check not legal"
        if k == FOLD:
            if legal.can_check:   # never fold for free
                return Action(CHECK), "fold when check is free"
            return action, None
        if k == CALL:
            if legal.call_amount > 0:
                ok = int(action.amount) == legal.call_amount
                return Action(CALL, legal.call_amount), None if ok else f"call amount {action.amount}"
            if legal.can_check:
                return Action(CHECK), "call with nothing to call"
            return Action(FOLD), "call not legal"
        if k in (BET, RAISE):
            if not legal.can_raise:
                if legal.call_amount > 0:
                    return Action(CALL, legal.call_amount), "raise not legal"
                return safest(legal), "raise not legal"
            amt = int(action.amount)
            lo, hi = legal.min_raise_to, legal.max_raise_to
            fixed = max(lo, min(hi, amt))
            problem = None if lo <= amt <= hi and k == legal.raise_kind else f"{k} {amt} outside {lo}-{hi}"
            return Action(legal.raise_kind, fixed), problem
        return safest(legal), f"unknown kind {k!r}"
    except Exception as e:  # noqa: BLE001
        return safest(legal), f"validate error {e!r}"


class HandLogger:
    """One compact line per hand: id pos hole board | my actions | net.
    Decision lines carry eq/po/spr when the bot exposes `last`."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.f = open(path, "a", buffering=1)
        self.cur = []

    def decision(self, state, action, ms, info, problem):
        bits = f"{state.street[0]}:{action}"
        if info and "eq" in info:
            bits += f"(eq{info['eq']:.2f}"
            if "po" in info:
                bits += f" po{info['po']:.2f}"
            bits += f" {ms:.1f}ms)"
        if problem:
            bits += f"!{problem}"
        self.cur.append((state, bits))

    def hand(self, result, me):
        try:
            if not self.cur:
                return
            st = self.cur[0][0]
            board = cards_str(result.board) if result.board else "-"
            net = result.net.get(me, 0) if me is not None else 0
            line = (f"{result.hand_id} {st.position} {cards_str(st.hole)} [{board}] "
                    + " ".join(b for _, b in self.cur) + f" net={net:+d}")
            if result.showdown:
                line += " sd=" + ",".join(f"{result.names.get(s, s)}:{cards_str(h)}"
                                          for s, h in result.showdown.items())
            self.f.write(line + "\n")
        finally:
            self.cur = []

    def error(self, msg):
        self.f.write("ERR " + msg.replace("\n", " | ") + "\n")


class SafeBot(Bot):
    def __init__(self, bot, budget=0.015, hard_timeout=True, safety=0.6,
                 log_path=None, debug=False):
        self.bot = bot
        self.name = bot.name
        self.budget = budget            # seconds per decision (dealer's limit or our target)
        self.safety = safety            # share of the budget given to the bot's own deadline
        self.hard_timeout = hard_timeout
        self.debug = debug
        self.log = HandLogger(log_path) if log_path else None
        self.pool = ThreadPoolExecutor(max_workers=1) if hard_timeout else None
        self.busy = None                # a timed-out decision still running
        self.stats = dict(decisions=0, timeouts=0, errors=0, fixed=0, max_ms=0.0, skipped=0)

    def _decide(self, state, deadline):
        self.bot.deadline = deadline
        return self.bot.act(state)

    def act(self, state):
        t0 = time.perf_counter()
        deadline = t0 + self.budget * self.safety
        action, problem = None, None
        gc_was = gc.isenabled()
        gc.disable()
        try:
            if self.busy is not None and not self.busy.done():
                # previous decision overran and is still computing: do not stack work
                self.stats["skipped"] += 1
                problem = "skipped: previous decision still running"
            elif self.pool is not None:
                fut = self.pool.submit(self._decide, state, deadline)
                try:
                    action = fut.result(timeout=max(0.0, self.budget * 0.95 - (time.perf_counter() - t0)))
                    self.busy = None
                except FutureTimeout:
                    self.busy = fut
                    self.stats["timeouts"] += 1
                    problem = "timeout"
            else:
                action = self._decide(state, deadline)
        except Exception as e:  # noqa: BLE001
            self.stats["errors"] += 1
            problem = f"error {e!r}"
            if self.log:
                self.log.error(traceback.format_exc())
        finally:
            if gc_was:
                gc.enable()
        if action is None:
            action = safest(state.legal)
        action, vproblem = validate(action, state.legal)
        if vproblem:
            self.stats["fixed"] += 1
            problem = problem or vproblem
        ms = (time.perf_counter() - t0) * 1000
        self.stats["decisions"] += 1
        self.stats["max_ms"] = max(self.stats["max_ms"], ms)
        info = getattr(self.bot, "last", None) if problem is None else None
        if self.log:
            self.log.decision(state, action, ms, info, problem)
        if self.debug:
            print(debug_line(state, action, ms, info, problem), file=sys.stderr)
        if hasattr(self.bot, "last"):
            self.bot.last = {}   # never log stale info next time
        return action

    def hand_over(self, result):
        try:
            if self.busy is not None:   # let an overrunning decision finish before updating state
                self.busy.result(timeout=1.0)
                self.busy = None
        except Exception:  # noqa: BLE001
            pass
        try:
            self.bot.hand_over(result)
        except Exception as e:  # noqa: BLE001
            self.stats["errors"] += 1
            if self.log:
                self.log.error(f"hand_over: {e!r}")
        if self.log:
            me = next((s for s, n in result.names.items() if n == self.name), None)
            self.log.hand(result, me)
        gc.collect(0)


def debug_line(state, action, ms, info, problem=None) -> str:
    """equity, pot odds, SPR and rough EV of fold / call / raise (no fold equity)."""
    info = info or {}
    eq = info.get("eq")
    pot, tc = state.pot, state.to_call
    parts = [f"DBG h{state.hand_id} {state.street} {state.position} {cards_str(state.hole)}",
             f"[{cards_str(state.board)}]" if state.board else "[]",
             f"pot={pot} call={tc} spr={state.spr:.1f} po={state.pot_odds:.2f}"]
    if eq is not None:
        parts.append(f"eq={eq:.3f}" + (f" eqc={info['eq_call']:.3f}" if "eq_call" in info else ""))
        ev_call = eq * (pot + tc) - tc
        risk = max(state.legal.min_raise_to - state.my_street_bet, 0)
        ev_raise = eq * (pot + 2 * risk) - risk if state.legal.can_raise else float("nan")
        parts.append(f"EV fold=0 {'call' if tc else 'check'}={ev_call:+.0f} raise(min)={ev_raise:+.0f}")
    parts.append(f"-> {action} {ms:.1f}ms")
    if problem:
        parts.append(f"!{problem}")
    return " ".join(parts)
