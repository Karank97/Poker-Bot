from bots import REGISTRY, make_bot
from bots.zoo.styles import Tilt
from core.state import HandResult
from sim.engine import Table


def test_every_zoo_bot_plays_clean():
    kinds = list(REGISTRY)
    for start in range(0, len(kinds), 8):
        group = kinds[start:start + 8]
        if len(group) < 2:
            group = group + ["call"]
        bots = [make_bot(k, f"{k}_{i}", seed=i) for i, k in enumerate(group)]
        t = Table(bots, seed=start)
        for h in range(300):
            t.play_hand(h % len(bots))
        for b in bots:
            st = t.stats[b.name]
            assert st.errors == 0, (b.name, st.last_error)
            assert st.illegal == 0, b.name


def test_tilt_gets_looser_after_losses():
    t = Tilt("tilt")
    calm = t.vpip
    for i in range(20):
        t.hand_over(HandResult(i, 0, {0: "tilt"}, {}, [], [], {}, {0: -1000}, {0: 10000}))
    assert t.vpip > calm * 2 and t.bluff > 0.5

