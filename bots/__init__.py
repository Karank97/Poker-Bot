"""Bot registry: make_bot('call', seed=1) -> bot instance."""
from bots.zoo import simple

REGISTRY = {
    "fold": simple.FoldBot,
    "call": simple.CallBot,
    "raise": simple.RaiseBot,
    "allin": simple.AllInBot,
    "random": simple.RandomBot,
}


def make_bot(kind, name=None, seed=0):
    if kind not in REGISTRY:
        raise KeyError(f"unknown bot {kind!r}; have {sorted(REGISTRY)}")
    return REGISTRY[kind](name=name or kind, seed=seed)
