"""Bot registry: make_bot('call', seed=1) -> bot instance."""
from bots import main_bot, opp_model
from core.safety import SafeBot
from bots.zoo import simple, styles

REGISTRY = {
    "fold": simple.CheckFold,
    "call": simple.CallBot,
    "raise": simple.RaiseBot,
    "allin": simple.AllInBot,
    "random": simple.RandomBot,
    "nit": styles.Nit,
    "lp": styles.LoosePassive,
    "tag": styles.TAG,
    "lag": styles.LAG,
    "foldbot": styles.FoldBot,
    "riverrat": styles.RiverRat,
    "tilt": styles.Tilt,
    "gtolite": styles.GTOLite,
    "potodds": styles.PotOdds,
    "mc": styles.MonteCarlo,
    "bayes": styles.Bayes,
    "main": main_bot.MainBot,
    "model": opp_model.ModelBot,
    # the event-day configuration: model bot behind the safety wrapper
    "safe": lambda name=None, seed=0: SafeBot(opp_model.ModelBot(name or "safe", seed)),
}
ZOO = [k for k in REGISTRY if k not in ("main", "model", "safe")]


def make_bot(kind, name=None, seed=0):
    if kind not in REGISTRY:
        raise KeyError(f"unknown bot {kind!r}; have {sorted(REGISTRY)}")
    return REGISTRY[kind](name=name or kind, seed=seed)
