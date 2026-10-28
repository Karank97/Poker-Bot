"""Event-day adapter: the ONLY file to rewrite on the day.

    python adapter/template.py                       # stdin/stdout line protocol (default)
    python adapter/template.py --tcp HOST PORT       # line protocol over a TCP socket
    python adapter/template.py --jsonl HOST PORT     # line-delimited JSON over a TCP socket

Pipeline: dealer line -> Tracker events -> GameState -> SafeBot(ModelBot) ->
Action -> serialize() -> reply line. Every `ACT?` gets exactly one reply that is
one of the verbs listed on that ACT? line, even if parsing or the bot fails.

Search for "TODO(day)" for everything that must be checked against the real
dealer. Messages marked OBSERVED were seen on the official site; ASSUMED ones
are guesses, harmless if the dealer never sends them (unknown lines are ignored
and logged).
"""
import json
import os
import re
import socket
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.join(ROOT, "vendor"))   # bundled treys fallback

from core.cards import parse_cards  # noqa: E402
from core.safety import SafeBot, validate  # noqa: E402
from core.state import (AGGRESSIVE, BET, CALL, CHECK, FOLD, RAISE, Action, GameState,  # noqa: E402
                        HandResult, HistoryEntry, Legal, position_names, seat_positions)


# =================================================================== CONFIG
class Config:
    BOT_NAME = "hero"
    HELLO = None                  # TODO(day): first line to send on connect, e.g. "JOIN teamname token"
    BUDGET = 0.015                # TODO(day): seconds per decision we allow ourselves (dealer limit?)
    HARD_TIMEOUT = True
    SMALL_BLIND, BIG_BLIND = 50, 100
    STARTING_STACK = 10000
    PERSIST_STACKS = False        # TODO(day): do stacks carry over between hands at the 8-max table?
    NUM_PLAYERS = 8
    MY_SEAT = 0                   # overwritten by a SEAT message if the dealer sends one
    DEFAULT_POSITION = "MP"       # used when position cannot be inferred
    ASSUMED_POSTFLOP_OPPONENTS = 2  # when the dealer does not report folds
    # amount conventions -------------------------------------------------
    RAISE_SEMANTICS = "to"        # TODO(day): "to" (total street bet) or "by" (increment over the bet)
    LEGAL_SEMANTICS = "to"        # same question for bounds printed on ACT? lines
    ACTION_SEMANTICS = "to"       # same for opponent ACTION lines
    CALL_WITH_AMOUNT = True       # OBSERVED: reply "CALL 50"
    REPLY_UPPERCASE = True
    ECHOES_OWN_ACTIONS = False    # TODO(day): does the dealer echo our own action back as an ACTION line?
    FOLD_ALWAYS_ALLOWED = False   # if True, FOLD may be sent even if not listed on ACT?
    # logging ------------------------------------------------------------
    LOG_PATH = os.path.join(ROOT, "logs", "hands.log")
    RAW_LOG_PATH = os.path.join(ROOT, "logs", "raw.log")   # every line in and out
    DEBUG = False                 # per-decision debug line on stderr


def make_bot(cfg):
    """TODO(day): swap the bot here if needed.
    Plumbing first (minutes 5-10): `from core.bot import Bot; return SafeBot(Bot(), budget=cfg.BUDGET)`
    (check/fold only). No-model run: bots.main_bot.MainBot instead of ModelBot."""
    from bots.opp_model import ModelBot
    return SafeBot(ModelBot(cfg.BOT_NAME, seed=int(time.time()) % 100000),
                   budget=cfg.BUDGET, hard_timeout=cfg.HARD_TIMEOUT,
                   log_path=cfg.LOG_PATH, debug=cfg.DEBUG)


# =================================================================== ACT? parsing
_VERB = r"(FOLD|CHECK|CALL|BET|RAISE|ALL[-_ ]?IN)"
_TOKEN_RE = re.compile(_VERB + r"(?:\s+(\d+)(?:\s*(?:-|\.\.|:|to)\s*(\d+))?)?", re.I)


def parse_legal_tokens(text):
    """'CALL 50/RAISE 200-10000/FOLD' -> {'CALL': (50, None), 'RAISE': (200, 10000), 'FOLD': (None, None)}"""
    out = {}
    for verb, a, b in _TOKEN_RE.findall(text):
        v = verb.upper().replace("-", "").replace("_", "").replace(" ", "")
        out[v] = (int(a) if a else None, int(b) if b else None)
    return out


# =================================================================== tracker
class Tracker:
    """Builds GameState / HandResult from dealer events. Works with only the
    OBSERVED messages; every ASSUMED message just makes the state more accurate."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.hand_id = 0
        self.my_seat = cfg.MY_SEAT
        self.n_players = cfg.NUM_PLAYERS
        self.names = {}               # seat -> name
        self.seat_of = {}             # name -> seat
        self.button = None
        self.stacks = {}
        self.my_stack = cfg.STARTING_STACK
        self.in_hand = False
        self._new_hand_state()

    def _new_hand_state(self):
        c = self.cfg
        self.hole, self.board, self.street = [], [], "preflop"
        self.history, self.showdown = [], {}
        self.pot = None               # dealer-reported pot, if any
        self.est_pot = c.SMALL_BLIND + c.BIG_BLIND
        self.current_bet = c.BIG_BLIND
        self.last_raise = c.BIG_BLIND
        self.street_bets = {}
        self.my_street_bet = 0
        self.my_committed = 0
        self.folded = set()
        self.saw_actions = False
        self.net = None
        self.start_stack = self.my_stack if c.PERSIST_STACKS else c.STARTING_STACK
        if not c.PERSIST_STACKS:
            self.my_stack = c.STARTING_STACK
        self.start_stacks = dict(self.stacks) if self.stacks else {}

    # ------------------------------------------------------------ helpers
    def seat(self, who):
        """Seat id from a seat number or a player name (names get new ids)."""
        if who is None:
            return None
        if re.fullmatch(r"\d+", str(who)):
            return int(who)
        if who not in self.seat_of:
            s = max(list(self.names) + [self.n_players - 1, self.my_seat]) + 1
            self.seat_of[who], self.names[s] = s, who
        return self.seat_of[who]

    def _to_total(self, seat, kind, amount, semantics):
        cur = self.street_bets.get(seat, 0)
        if kind in AGGRESSIVE and semantics == "by":
            return self.current_bet + amount
        if kind == CALL:
            return cur + amount
        return amount

    def _apply(self, seat, kind, amount):
        """Record an action (ours or theirs) and keep pot / bets in sync."""
        cur = self.street_bets.get(seat, 0)
        if kind == CALL:
            # chips added = the outstanding bet (TODO(day): check what the dealer's CALL amount means)
            add = max(0, self.current_bet - cur) or amount
            self.street_bets[seat] = cur + add
            self.est_pot += add
            self.history.append(HistoryEntry(seat, self.street, CALL, add))
            if seat == self.my_seat:
                self.my_street_bet += add
                self.my_committed += add
                self.my_stack -= add
        elif kind in AGGRESSIVE:
            total = amount
            add = max(0, total - cur)
            self.last_raise = max(self.last_raise, total - self.current_bet)
            self.current_bet = max(self.current_bet, total)
            self.street_bets[seat] = total
            self.est_pot += add
            self.history.append(HistoryEntry(seat, self.street, kind, total))
            if seat == self.my_seat:
                self.my_street_bet = total
                self.my_committed += add
                self.my_stack -= add
        elif kind == FOLD:
            self.folded.add(seat)
            self.history.append(HistoryEntry(seat, self.street, FOLD))
        elif kind == CHECK:
            self.history.append(HistoryEntry(seat, self.street, CHECK))

    def _new_street(self, street):
        self.street = street
        self.current_bet = 0
        self.last_raise = self.cfg.BIG_BLIND
        self.street_bets = {}
        self.my_street_bet = 0

    def all_seats(self):
        seats = set(range(self.n_players)) | set(self.names) | {self.my_seat}
        return sorted(seats)

    # ------------------------------------------------------------ positions
    def positions(self, legal):
        seats = self.all_seats()
        if self.button is not None and self.button in seats and 2 <= len(seats) <= 8:
            return seat_positions(seats, self.button)
        # infer our own position from the preflop price (TODO(day): better with BUTTON)
        pos = self.cfg.DEFAULT_POSITION
        raised = any(h.kind in AGGRESSIVE for h in self.history if h.street == "preflop")
        if self.street == "preflop" and not raised:
            if legal.can_check:
                pos = "BB"
            elif legal.call_amount == self.cfg.BIG_BLIND - self.cfg.SMALL_BLIND:
                pos = "SB"
        names = position_names(min(8, max(2, len(seats))))
        others = [n for n in names if n != pos]
        out = {self.my_seat: pos}
        for s, n in zip([s for s in seats if s != self.my_seat], others):
            out[s] = n
        return out

    def live(self):
        seats = [s for s in self.all_seats() if s not in self.folded]
        if self.street != "preflop" and not self.saw_actions:
            k = self.cfg.ASSUMED_POSTFLOP_OPPONENTS
            others = [s for s in seats if s != self.my_seat][:k]
            return [self.my_seat] + others
        return seats

    def infer_blind_post(self, legal):
        """Without ACTION post lines we do not know we posted a blind; infer it
        from the price of our first preflop decision."""
        c = self.cfg
        if self.street != "preflop" or self.my_street_bet or any(
                h.seat == self.my_seat for h in self.history):
            return
        raised = any(h.kind in AGGRESSIVE for h in self.history)
        post = 0
        if not raised and legal.can_check:
            post = c.BIG_BLIND
        elif not raised and legal.call_amount == c.BIG_BLIND - c.SMALL_BLIND:
            post = c.SMALL_BLIND
        if post:
            self.my_street_bet = self.my_committed = post
            self.my_stack -= post
            self.street_bets[self.my_seat] = post

    def infer_hidden_bet(self, legal):
        """The price says someone bet more than we have seen (the dealer does not
        report actions, or we missed one): record an unknown aggressor so the bot
        knows it faces a bet / raise and how big it is."""
        if not legal.call_amount:
            return
        faced = self.my_street_bet + legal.call_amount
        if faced <= self.current_bet and any(h.kind in AGGRESSIVE for h in self.history
                                             if h.street == self.street):
            return
        if self.street == "preflop" and faced <= self.cfg.BIG_BLIND:
            self.current_bet = max(self.current_bet, faced)
            return
        others = [s for s in self.all_seats() if s != self.my_seat and s not in self.folded]
        seat = others[0] if others else self.my_seat + 1
        kind = RAISE if self.street == "preflop" or self.current_bet else BET
        self.est_pot += max(0, faced - self.street_bets.get(seat, 0))
        self.street_bets[seat] = faced
        self.last_raise = max(self.last_raise, faced - self.current_bet)
        self.current_bet = faced
        self.history.append(HistoryEntry(seat, self.street, kind, faced))

    # ------------------------------------------------------------ build
    def legal_from(self, tokens):
        c = self.cfg
        lg = Legal(can_fold="FOLD" in tokens or c.FOLD_ALWAYS_ALLOWED, can_check="CHECK" in tokens)
        if "CALL" in tokens:
            amt = tokens["CALL"][0]
            lg.call_amount = amt if amt else max(1, self.current_bet - self.my_street_bet)
        raise_verb = "RAISE" if "RAISE" in tokens else ("BET" if "BET" in tokens else None)
        my_max = self.my_stack + self.my_street_bet
        if raise_verb:
            lo, hi = tokens[raise_verb]
            if lo is not None and c.LEGAL_SEMANTICS == "by":
                lo = self.current_bet + lo
                hi = self.current_bet + hi if hi is not None else None
            lg.can_raise = True
            lg.raise_kind = RAISE if raise_verb == "RAISE" else BET
            lg.min_raise_to = lo if lo is not None else min(my_max, self.current_bet + self.last_raise)
            lg.max_raise_to = hi if hi is not None else max(lg.min_raise_to, my_max)
        elif "ALLIN" in tokens:
            lg.can_raise = True
            lg.raise_kind = RAISE if self.current_bet else BET
            lg.min_raise_to = lg.max_raise_to = max(my_max, self.current_bet)
        if not lg.can_check and not lg.call_amount and not lg.can_raise:
            lg.can_fold = True
        return lg

    def game_state(self, legal):
        to_call = legal.call_amount
        if not to_call and not legal.can_check:
            to_call = max(0, self.current_bet - self.my_street_bet)
        pot = self.pot if self.pot is not None else max(self.est_pot, self.my_committed + to_call)
        stacks = {s: self.stacks.get(s, self.cfg.STARTING_STACK) for s in self.all_seats()}
        stacks[self.my_seat] = self.my_stack
        return GameState(
            hole=list(self.hole), board=list(self.board), street=self.street, pot=pot,
            to_call=to_call, min_raise_to=legal.min_raise_to, max_raise_to=legal.max_raise_to,
            my_seat=self.my_seat, my_stack=self.my_stack, my_street_bet=self.my_street_bet,
            big_blind=self.cfg.BIG_BLIND, small_blind=self.cfg.SMALL_BLIND, stacks=stacks,
            positions=self.positions(legal), names=dict(self.names), live=self.live(),
            history=list(self.history), legal=legal, hand_id=self.hand_id, button=self.button)

    def hand_result(self):
        seats = self.all_seats()
        start = {s: self.start_stacks.get(s, self.cfg.STARTING_STACK) for s in seats}
        start[self.my_seat] = self.start_stack
        net = {s: 0 for s in seats}
        net[self.my_seat] = self.net if self.net is not None else 0
        names = {s: self.names.get(s, f"seat{s}") for s in seats}
        names[self.my_seat] = self.cfg.BOT_NAME
        legal = Legal(can_check=True)
        return HandResult(self.hand_id, self.button, names, self.positions(legal), list(self.board),
                          list(self.history), dict(self.showdown), net, start)


# =================================================================== protocol
class Protocol:
    def __init__(self, cfg=Config, bot=None, raw_log=True):
        self.cfg = cfg
        self.t = Tracker(cfg)
        self.bot = bot if bot is not None else make_bot(cfg)
        self.raw = None
        if raw_log and cfg.RAW_LOG_PATH:
            os.makedirs(os.path.dirname(cfg.RAW_LOG_PATH), exist_ok=True)
            self.raw = open(cfg.RAW_LOG_PATH, "a", buffering=1)

    def log(self, direction, line):
        if self.raw:
            self.raw.write(f"{time.time():.3f} {direction} {line}\n")

    def _finish_hand(self):
        if self.t.in_hand:
            self.t.in_hand = False
            try:
                self.bot.hand_over(self.t.hand_result())
            except Exception:  # noqa: BLE001
                self.log("ERR", traceback.format_exc().replace("\n", " | "))

    # ------------------------------------------------------------ events
    def handle_line(self, line):
        """One dealer line in, an optional reply line out. Never raises."""
        line = line.strip()
        if not line:
            return None
        self.log("<", line)
        head, _, rest = line.partition(" ")
        head = head.upper()
        try:
            reply = self.event(head, rest.strip())
        except Exception:  # noqa: BLE001
            self.log("ERR", traceback.format_exc().replace("\n", " | "))
            reply = emergency_reply(rest) if head in ("ACT?", "ACT") else None
        if reply is not None:
            self.log(">", reply)
        return reply

    def event(self, head, rest):
        t, c = self.t, self.cfg
        if head == "DEAL":                                   # OBSERVED: DEAL As Kd
            self._finish_hand()
            t.hand_id += 1
            t._new_hand_state()
            t.in_hand = True
            t.hole = parse_cards(rest)
            t.street_bets = {}
            return None
        if head in ("FLOP", "TURN", "RIVER"):                # OBSERVED: FLOP 2c 7d 9h / TURN 5s
            cards = parse_cards(rest)
            t.board = cards if len(cards) >= 3 else t.board + cards
            t._new_street(head.lower())
            return None
        if head in ("ACT?", "ACT"):                          # OBSERVED: ACT? CHECK/BET, ACT? CALL 50
            return self.act(rest)
        # ---------------- ASSUMED messages (TODO(day): rename to the real ones)
        if head == "SEAT":                                   # SEAT 3
            t.my_seat = int(rest.split()[0])
        elif head == "BUTTON":                               # BUTTON 5  (seat or name)
            t.button = t.seat(rest.split()[0])
        elif head == "PLAYERS":                              # PLAYERS alice bob hero ...
            for i, nm in enumerate(rest.split()):
                t.names[i], t.seat_of[nm] = nm, i
                if nm == c.BOT_NAME:
                    t.my_seat = i
            t.n_players = len(rest.split())
        elif head == "BLINDS":                               # BLINDS 50 100
            sb, bb = (int(x) for x in rest.split()[:2])
            c.SMALL_BLIND, c.BIG_BLIND = sb, bb
        elif head == "STACK":                                # STACK 9850 (ours)
            t.my_stack = int(rest.split()[0])
        elif head == "STACKS":                               # STACKS 0:10000 1:9800 ...
            for tok in rest.split():
                who, _, amt = tok.partition(":")
                t.stacks[t.seat(who)] = int(amt)
            if t.my_seat in t.stacks:
                t.my_stack = t.stacks[t.my_seat]
        elif head == "POT":                                  # POT 450
            t.pot = int(rest.split()[0])
        elif head == "ACTION":                               # ACTION <seat|name> RAISE 300
            parts = rest.split()
            seat = t.seat(parts[0])
            kind = parts[1].lower()
            kind = {"allin": RAISE, "all-in": RAISE, "post": "post"}.get(kind, kind)
            amount = int(parts[2]) if len(parts) > 2 else 0
            if seat == t.my_seat and not c.ECHOES_OWN_ACTIONS:
                return None
            if seat == t.my_seat:
                pass   # echoed own action: our record already has it
            elif kind == "post":
                t.street_bets[seat] = amount
                t.history.append(HistoryEntry(seat, "preflop", "post", amount))
            else:
                t.saw_actions = True
                if kind in AGGRESSIVE:
                    amount = t._to_total(seat, kind, amount, c.ACTION_SEMANTICS)
                t._apply(seat, kind, amount)
        elif head == "SHOWDOWN":                             # SHOWDOWN <seat|name> As Kd
            who, _, cards = rest.partition(" ")
            t.showdown[t.seat(who)] = parse_cards(cards)
        elif head in ("RESULT", "WIN", "END"):               # END [our net]
            nums = re.findall(r"-?\d+", rest)
            if head == "END" and nums:
                t.net = int(nums[-1])
            self._finish_hand()
        else:
            self.log("?", f"unknown message {head}")
        return None

    def act(self, rest):
        t = self.t
        tokens = parse_legal_tokens(rest)
        if not tokens:
            return emergency_reply(rest)
        legal = t.legal_from(tokens)
        t.infer_blind_post(legal)
        t.infer_hidden_bet(legal)
        state = t.game_state(legal)
        action = self.bot.act(state)
        action, _ = validate(action, legal)
        reply = serialize(action, tokens, self.cfg, t.current_bet)
        # record our own action so pot / history stay right
        if action.kind == CALL:
            t._apply(t.my_seat, CALL, action.amount)
        elif action.kind in (BET, RAISE):
            t._apply(t.my_seat, action.kind, action.amount)
        else:
            t._apply(t.my_seat, action.kind, 0)
        return reply


def serialize(action, tokens, cfg, current_bet):
    """Action -> reply line using only verbs listed on the ACT? line."""
    k = action.kind.upper()
    if k in ("BET", "RAISE"):
        verb = "RAISE" if "RAISE" in tokens else ("BET" if "BET" in tokens else None)
        if verb is None and "ALLIN" in tokens:
            return _case("ALLIN", cfg)
        if verb is None:
            k = "CALL" if "CALL" in tokens else "CHECK"
        else:
            amt = action.amount - current_bet if cfg.RAISE_SEMANTICS == "by" else action.amount
            return _case(f"{verb} {int(amt)}", cfg)
    if k == "CALL" and "CALL" in tokens:
        amt = tokens["CALL"][0] or action.amount
        return _case(f"CALL {amt}" if cfg.CALL_WITH_AMOUNT and amt else "CALL", cfg)
    if k in tokens or (k == "FOLD" and cfg.FOLD_ALWAYS_ALLOWED):
        return _case(k, cfg)
    for fallback in ("CHECK", "FOLD", "CALL"):
        if fallback in tokens:
            return serialize(Action(fallback.lower(), tokens[fallback][0] or 0), tokens, cfg, current_bet)
    return _case(next(iter(tokens)), cfg)


def _case(s, cfg):
    return s.upper() if cfg.REPLY_UPPERCASE else s.lower()


def emergency_reply(rest):
    """Last resort when everything else failed: CHECK if offered, else FOLD."""
    up = rest.upper()
    if "CHECK" in up:
        return "CHECK"
    if "FOLD" in up or "CALL" not in up:
        return "FOLD"
    m = re.search(r"CALL\s+(\d+)", up)
    return f"CALL {m.group(1)}" if m else "CALL"


# =================================================================== transports
def run_stdio(proto):
    """Worked example 1: dealer writes lines to our stdin, we reply on stdout."""
    if proto.cfg.HELLO:
        print(proto.cfg.HELLO, flush=True)
    for line in sys.stdin:
        reply = proto.handle_line(line)
        if reply is not None:
            sys.stdout.write(reply + "\n")
            sys.stdout.flush()
    proto._finish_hand()


def run_tcp(proto, host, port, jsonl=False):
    """Worked example 2: line-delimited text or JSON over a TCP socket.
    JSON mapping (TODO(day)): {"type": "deal", "cards": ["As","Kd"]},
    {"type": "flop", "cards": [...]}, {"type": "act", "legal": "CHECK/BET 100-10000"},
    any other {"type": X, ...} -> event X with its fields joined as text.
    Reply: {"action": "RAISE", "amount": 300}."""
    with socket.create_connection((host, port)) as sock:
        f = sock.makefile("rwb", buffering=0)
        if proto.cfg.HELLO:
            f.write((proto.cfg.HELLO + "\n").encode())
        for raw in f:
            text = raw.decode(errors="replace").strip()
            if not text:
                continue
            if jsonl:
                try:
                    line = json_to_line(json.loads(text))
                except Exception:  # noqa: BLE001
                    proto.log("ERR", f"bad json {text!r}")
                    continue
            else:
                line = text
            reply = proto.handle_line(line)
            if reply is not None:
                out = json.dumps(line_to_json(reply)) if jsonl else reply
                f.write((out + "\n").encode())
    proto._finish_hand()


def json_to_line(msg):
    """TODO(day): adapt to the real JSON schema."""
    typ = str(msg.get("type", "")).upper()
    if typ in ("ACT", "ACT?", "ACTION_REQUEST"):
        legal = msg.get("legal", "")
        if isinstance(legal, list):
            legal = "/".join(legal)
        return "ACT? " + legal
    if "cards" in msg:
        cards = msg["cards"]
        return f"{typ} " + (" ".join(cards) if isinstance(cards, list) else str(cards))
    rest = " ".join(str(v) for k, v in msg.items() if k != "type")
    return f"{typ} {rest}"


def line_to_json(reply):
    verb, _, amt = reply.partition(" ")
    out = {"action": verb}
    if amt:
        out["amount"] = int(amt)
    return out


def warm_up(proto):
    """Load tables and caches before the first real decision."""
    try:
        from core.state import Legal as L
        st = GameState(hole=parse_cards("AsKd"), board=parse_cards("2c7d9h"), street="flop", pot=300,
                       to_call=100, my_seat=0, my_stack=9900, stacks={0: 9900, 1: 9900}, live=[0, 1],
                       positions={0: "BTN", 1: "BB"},
                       legal=L(call_amount=100, can_raise=True, min_raise_to=200, max_raise_to=9900))
        proto.bot.bot.act(st)
        st.street, st.board = "preflop", []
        proto.bot.bot.act(st)
    except Exception:  # noqa: BLE001
        proto.log("ERR", "warm-up failed " + traceback.format_exc().replace("\n", " | "))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--debug" in argv:
        Config.DEBUG = True
        argv = [a for a in argv if a != "--debug"]
    proto = Protocol(Config)
    warm_up(proto)
    if argv[:1] == ["--tcp"]:
        run_tcp(proto, argv[1], int(argv[2]))
    elif argv[:1] == ["--jsonl"]:
        run_tcp(proto, argv[1], int(argv[2]), jsonl=True)
    else:
        run_stdio(proto)


if __name__ == "__main__":
    main()
