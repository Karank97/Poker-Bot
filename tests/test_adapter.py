import subprocess
import sys

from adapter.template import (Config, Protocol, emergency_reply, parse_legal_tokens, serialize)
from core.bot import Bot
from core.state import BET, CALL, CHECK, FOLD, RAISE, Action


class Cfg(Config):
    RAW_LOG_PATH = None
    LOG_PATH = None


def proto(bot=None):
    from bots.opp_model import ModelBot
    from core.safety import SafeBot
    return Protocol(Cfg, bot or SafeBot(ModelBot("hero", 1)), raw_log=False)


def test_parse_legal_variants():
    assert parse_legal_tokens("CALL 50") == {"CALL": (50, None)}
    assert parse_legal_tokens("CHECK/RAISE") == {"CHECK": (None, None), "RAISE": (None, None)}
    t = parse_legal_tokens("FOLD/CALL 100/RAISE 200-10000")
    assert t["RAISE"] == (200, 10000) and t["CALL"] == (100, None) and "FOLD" in t
    assert parse_legal_tokens("fold, call 100, raise 200..9000")["RAISE"] == (200, 9000)
    assert "ALLIN" in parse_legal_tokens("FOLD/ALL-IN")


def test_serialize_only_listed_verbs():
    toks = parse_legal_tokens("CHECK/BET 100-10000")
    assert serialize(Action(RAISE, 300), toks, Cfg, 0) == "BET 300"
    assert serialize(Action(FOLD), toks, Cfg, 0) == "CHECK"
    toks = parse_legal_tokens("CALL 50")
    assert serialize(Action(FOLD), toks, Cfg, 0) == "CALL 50"       # FOLD not offered
    toks = parse_legal_tokens("FOLD/CALL 100")
    assert serialize(Action(RAISE, 500), toks, Cfg, 0) == "CALL 100"
    toks = parse_legal_tokens("FOLD/ALLIN")
    assert serialize(Action(RAISE, 10000), toks, Cfg, 0) == "ALLIN"


def test_raise_by_semantics():
    class By(Cfg):
        RAISE_SEMANTICS = "by"
    toks = parse_legal_tokens("FOLD/CALL 100/RAISE")
    assert serialize(Action(RAISE, 300), toks, By, 100) == "RAISE 200"


def test_emergency():
    assert emergency_reply("CHECK/BET") == "CHECK"
    assert emergency_reply("FOLD/CALL 50") == "FOLD"
    assert emergency_reply("CALL 50") == "CALL 50"


def test_hand_flow_minimal_messages():
    p = proto()
    assert p.handle_line("DEAL As Ah") is None
    r = p.handle_line("ACT? FOLD/CALL 100/RAISE 200-10000")
    assert r.split()[0] in ("RAISE", "CALL")          # AA never folds
    assert p.handle_line("FLOP 2c 7d 9h") is None
    r = p.handle_line("ACT? CHECK/BET 100-9700")
    assert r.split()[0] in ("CHECK", "BET")
    # facing a shove preflop with trash and no ACTION info: must not call off
    p.handle_line("DEAL 7c 2d")
    assert p.handle_line("ACT? FOLD/CALL 10000") == "FOLD"


def test_action_lines_get_no_reply_and_feed_the_model():
    p = proto()
    for line in ["PLAYERS a b hero", "SEAT 2", "BUTTON 2", "DEAL Ks Kd",
                 "ACTION a POST 50", "ACTION b POST 100"]:
        assert p.handle_line(line) is None
    assert p.handle_line("ACT? FOLD/CALL 100/RAISE 200-10000").startswith(("RAISE", "CALL"))
    assert p.handle_line("ACTION a FOLD") is None
    assert p.handle_line("END 150") is None
    assert p.bot.bot.model.profile(0, {0: "a"}).hands == 1


class Boom(Bot):
    name = "boom"

    def act(self, state):
        raise RuntimeError("x")


def test_crashing_bot_still_replies():
    p = proto(Boom())
    p.handle_line("DEAL As Kd")
    assert p.handle_line("ACT? CHECK/BET") == "CHECK"
    assert p.handle_line("ACT? FOLD/CALL 100") == "FOLD"
    assert p.handle_line("ACT? garbage") in ("FOLD", "CHECK")
    assert p.handle_line("TOTALLY UNKNOWN MESSAGE") is None


def test_stdio_subprocess_roundtrip():
    lines = "DEAL As Ah\nACT? FOLD/CALL 100/RAISE 200-10000\nFLOP 2c 7d 9h\nACT? CHECK/BET 100-9000\n"
    out = subprocess.run([sys.executable, "adapter/template.py"], input=lines, capture_output=True,
                         text=True, timeout=60).stdout.split("\n")
    out = [l for l in out if l]
    assert len(out) == 2 and out[0].split()[0] in ("RAISE", "CALL") and out[1].split()[0] in ("CHECK", "BET")
