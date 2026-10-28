"""Fake dealer: runs the real engine and talks the text protocol to a bot
subprocess over stdin/stdout, so the adapter can be tested end to end.

  python -m adapter.fake_dealer --hands 300
  python -m adapter.fake_dealer --minimal            # only OBSERVED messages (DEAL/FLOP/TURN/RIVER/ACT?)
  python -m adapter.fake_dealer --cmd "python dist/bot/main.py"

Messages sent (the ASSUMED ones only without --minimal):
  PLAYERS n1 n2 ...  SEAT i  BUTTON b  STACK x     at hand start
  DEAL c1 c2 / FLOP a b c / TURN d / RIVER e
  ACTION <name> <KIND> [amount]                    every other player's action (raise amounts are "to")
  POT p
  ACT? FOLD/CALL 100/RAISE 200-10000               when it is the bot's turn
  SHOWDOWN <name> c1 c2 / END <net>                at hand end
"""
import argparse
import os
import select
import shlex
import subprocess
import sys
import time

from bots import make_bot
from core.bot import Bot
from core.cards import cards_str
from core.state import BET, CALL, CHECK, FOLD, RAISE, Action
from sim.run import print_rows, run_match


def legal_line(lg):
    parts = []
    if lg.can_fold and not lg.can_check:
        parts.append("FOLD")
    if lg.can_check:
        parts.append("CHECK")
    if lg.call_amount:
        parts.append(f"CALL {lg.call_amount}")
    if lg.can_raise:
        parts.append(f"{lg.raise_kind.upper()} {lg.min_raise_to}-{lg.max_raise_to}")
    return "/".join(parts)


class RemoteBot(Bot):
    def __init__(self, cmd, name="hero", minimal=False, timeout=1.0):
        self.name = name
        self.minimal = minimal
        self.timeout = timeout
        self.proc = subprocess.Popen(shlex.split(cmd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=sys.stderr, text=True, bufsize=1)
        self.hand = None
        self.board_len = 0
        self.sent = 0
        self.rtts = []
        self.bad_replies = 0
        self.timeouts = 0

    def send(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def _sync(self, state_or_result, history, names, board):
        for h in history[self.sent:]:
            if h.seat == self._my_seat:
                continue
            if self.minimal:
                continue
            amt = f" {h.amount}" if h.kind in (CALL, BET, RAISE, "post") else ""
            self.send(f"ACTION {names[h.seat]} {h.kind.upper()}{amt}")
        self.sent = len(history)

    def act(self, state):
        if state.hand_id != self.hand:
            self.hand = state.hand_id
            self.board_len = 0
            self.sent = 0
            self._my_seat = state.my_seat
            if not self.minimal:
                self.send("PLAYERS " + " ".join(state.names[s] for s in sorted(state.names)))
                self.send(f"SEAT {state.my_seat}")
                self.send(f"BUTTON {state.button}")
                self.send(f"STACK {state.my_stack + state.my_street_bet}")
            self.send("DEAL " + cards_str(state.hole))
        # actions that happened before the board changed belong to the earlier street
        streets = [h for h in state.history]
        if len(state.board) != self.board_len:
            before = [h for h in streets if h.street != state.street]
            self._sync(state, before, state.names, state.board)
            names = {3: "FLOP", 4: "TURN", 5: "RIVER"}
            if self.board_len == 0 and len(state.board) >= 3:
                self.send("FLOP " + cards_str(state.board[:3]))
                self.board_len = 3
            while self.board_len < len(state.board):
                self.board_len += 1
                self.send(f"{names[self.board_len]} {cards_str(state.board[self.board_len - 1:self.board_len])}")
        self._sync(state, streets, state.names, state.board)
        if not self.minimal:
            self.send(f"POT {state.pot}")
        t = time.perf_counter()
        self.send("ACT? " + legal_line(state.legal))
        ready, _, _ = select.select([self.proc.stdout], [], [], self.timeout)
        if not ready:
            self.timeouts += 1
            return Action(FOLD)
        reply = self.proc.stdout.readline().strip()
        self.rtts.append((time.perf_counter() - t) * 1000)
        return self._parse(reply, state.legal)

    def _parse(self, reply, lg):
        verb, _, amt = reply.upper().partition(" ")
        allowed = legal_line(lg).split("/")
        if not any(a.split()[0] == verb for a in allowed):
            self.bad_replies += 1
            return Action(FOLD)
        if verb == "CALL":
            return Action(CALL, lg.call_amount)
        if verb in ("BET", "RAISE"):
            return Action(verb.lower(), int(amt))
        return Action(verb.lower())

    def hand_over(self, result):
        # the bot never acted this hand (folded around to it never happens, but blinds may walk)
        if result.hand_id != self.hand:
            return
        if not self.minimal:
            self._sync(None, result.history, result.names, result.board)
            for s, h in result.showdown.items():
                self.send(f"SHOWDOWN {result.names[s]} {cards_str(h)}")
        me = next(s for s, n in result.names.items() if n == self.name)
        self.send(f"END {result.net[me]}")

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cmd", default=f"{sys.executable} adapter/template.py")
    ap.add_argument("--hands", type=int, default=300)
    ap.add_argument("--opps", default="tag,lag,call,nit,potodds,allin,riverrat")
    ap.add_argument("--minimal", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    remote = RemoteBot(a.cmd, minimal=a.minimal)
    bots = [remote] + [make_bot(k, f"{k}{i}", seed=i) for i, k in enumerate(a.opps.split(","))]
    try:
        rows, _ = run_match(bots, a.hands, duplicate=False, seed=a.seed)
    finally:
        remote.close()
    print_rows(rows)
    r = sorted(remote.rtts) or [0]
    print(f"hero round trips: n={len(remote.rtts)} p50={r[len(r) // 2]:.1f}ms "
          f"p99={r[int(len(r) * .99)]:.1f}ms max={r[-1]:.1f}ms "
          f"timeouts={remote.timeouts} bad_replies={remote.bad_replies}")
    return remote


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
