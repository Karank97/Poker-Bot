import time

from bots import make_bot
from core.bot import Bot
from core.safety import SafeBot, debug_line, validate
from core.state import BET, CALL, CHECK, FOLD, RAISE, Action, HandResult, Legal
from sim.engine import Table
from tests.test_main_bot import make_state

FACING = Legal(can_check=False, call_amount=100, can_raise=True, min_raise_to=200, max_raise_to=5000)
FREE = Legal(can_check=True, call_amount=0, can_raise=True, min_raise_to=100, max_raise_to=5000, raise_kind=BET)


def test_validate():
    assert validate(Action(CHECK), FACING)[0] == Action(FOLD)
    assert validate(Action(FOLD), FREE)[0] == Action(CHECK)            # never fold for free
    assert validate(Action(CALL, 999), FACING)[0] == Action(CALL, 100)
    assert validate(Action(CALL, 0), FREE)[0] == Action(CHECK)
    assert validate(Action(RAISE, 10**9), FACING)[0] == Action(RAISE, 5000)
    assert validate(Action(RAISE, 300), FREE)[0] == Action(BET, 300)   # kind fixed to BET
    no_raise = Legal(can_check=False, call_amount=100)
    assert validate(Action(RAISE, 500), no_raise)[0] == Action(CALL, 100)
    assert validate("garbage", FACING)[0] == Action(FOLD)
    assert validate(Action("dance"), FREE)[0] == Action(CHECK)
    assert validate(Action(RAISE, 300), FACING)[1] is None


class Crash(Bot):
    name = "crash"

    def act(self, state):
        raise ValueError("bug")


class Slow(Bot):
    name = "slow"

    def act(self, state):
        time.sleep(0.05)
        return Action(RAISE, 300)


def test_crash_becomes_check_or_fold():
    sb = SafeBot(Crash())
    assert sb.act(make_state("AsAh", to_call=100)) == Action(FOLD)
    assert sb.act(make_state("AsAh", ["2c", "3d", "4h"], to_call=0)) == Action(CHECK)
    assert sb.stats["errors"] == 2


def test_hard_timeout_and_no_stacking():
    sb = SafeBot(Slow(), budget=0.015)
    t = time.perf_counter()
    a = sb.act(make_state("AsAh", to_call=100))
    assert (time.perf_counter() - t) < 0.03 and a == Action(FOLD) and sb.stats["timeouts"] == 1
    # the slow decision is still running: next call returns the fallback at once
    assert sb.act(make_state("AsAh", to_call=100)) == Action(FOLD) and sb.stats["skipped"] == 1


def test_main_bot_respects_deadline():
    sb = SafeBot(make_bot("model", "m"), budget=0.015)
    st = make_state("Ah9h", ["Kh", "7h", "2c"], to_call=300, pot=900, live=list(range(8)))
    worst = 0
    for _ in range(30):
        t = time.perf_counter()
        sb.act(st)
        worst = max(worst, time.perf_counter() - t)
    assert worst < 0.015 and sb.stats["timeouts"] == 0


def test_hand_log_and_debug(tmp_path, capsys):
    path = tmp_path / "hands.log"
    hero = SafeBot(make_bot("model", "hero"), log_path=str(path), debug=True)
    t = Table([hero, make_bot("call"), make_bot("raise")], seed=1)
    for h in range(30):
        t.play_hand(h % 3)
    lines = path.read_text().splitlines()
    assert len(lines) >= 20 and all(" net=" in l for l in lines)
    out = capsys.readouterr()
    assert out.out == "" and "DBG" in out.err     # stdout stays clean for the protocol


def test_debug_line_fields():
    st = make_state("AsKs", ["Qs", "Js", "2d"], to_call=200, pot=600, live=[0, 1])
    line = debug_line(st, Action(CALL, 200), 1.2, {"eq": 0.7, "eq_call": 0.6})
    assert "eq=0.700" in line and "spr=" in line and "po=0.25" in line and "EV" in line
