from bots import make_bot
from bots.opp_model import ModelBot, OpponentModel
from core.state import CALL, FOLD, RAISE, HandResult, HistoryEntry
from sim.engine import Table


def shove_hand(i, shover_seat=1, caller=None):
    h = [HistoryEntry(2, "preflop", "post", 50), HistoryEntry(3, "preflop", "post", 100),
         HistoryEntry(shover_seat, "preflop", RAISE, 10000)]
    for s in (0, 2, 3):
        h.append(HistoryEntry(s, "preflop", FOLD))
    names = {0: "me", 1: "shovey", 2: "b", 3: "c"}
    return HandResult(i, 0, names, {}, [], h, {}, {0: 0, 1: 150, 2: -50, 3: -100},
                      {s: 10000 for s in range(4)})


def test_smoothing_and_shover():
    m = OpponentModel("me")
    m.update(shove_hand(0), my_seat=0)
    p = m.profile(1, {1: "shovey"})
    assert p.allin < 0.15 and p.archetype != "shover"     # one hand is not enough
    for i in range(1, 40):
        m.update(shove_hand(i), my_seat=0)
    p = m.profile(1, {1: "shovey"})
    assert p.allin > 0.7 and p.archetype == "shover" and p.conf > 0.6
    # the folders look like nits
    assert m.profile(2, {2: "b"}).vpip < 0.1


def test_identity_by_name_survives_seat_change():
    m = OpponentModel("me")
    for i in range(40):
        m.update(shove_hand(i, shover_seat=1), my_seat=0)
    # same bot name, new seat
    assert m.profile(3, {3: "shovey"}).archetype == "shover"
    # names hidden: falls back to seat stats
    m2 = OpponentModel("me", use_names=False)
    for i in range(40):
        m2.update(shove_hand(i, shover_seat=1), my_seat=0)
    assert m2.profile(1, {}).archetype == "shover"


def test_classifies_zoo():
    hero = ModelBot("HERO", seed=1)
    kinds = ["nit", "call", "lag", "riverrat", "foldbot", "allin", "tag"]
    bots = [hero] + [make_bot(k, k, seed=i) for i, k in enumerate(kinds)]
    t = Table(bots, seed=4)
    for h in range(1500):
        t.play_hand(h % 8)
    names = {i: b.name for i, b in enumerate(bots)}
    arch = {b.name: hero.model.profile(i, names).archetype for i, b in enumerate(bots) if i}
    assert arch["allin"] == "shover"
    assert arch["nit"] == "nit" and arch["foldbot"] == "nit"
    assert arch["call"] == "station" and arch["riverrat"] == "station"
    assert t.stats["HERO"].errors == 0 and t.stats["HERO"].illegal == 0


def test_adjust_vs_station_and_shover():
    hero = ModelBot("HERO", seed=1)
    for i in range(60):
        hero.hand_over(shove_hand(i))
    from tests.test_main_bot import make_state
    st = make_state("AsKd", to_call=9900, pot=10150, me=0, live=[0, 1],
                    history=shove_hand(0).history[:3])
    st.names = {0: "HERO", 1: "shovey"}
    adj = hero.adjust(st)
    assert adj.jam_width > 0.5        # calls a shover with a wide range assumption
