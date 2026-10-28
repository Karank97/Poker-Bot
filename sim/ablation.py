"""Ablation of the opponent model: full model, main bot, and the model with
each exploit switched off, over the same fields and seeds.

  python -m sim.ablation --hands 6000
"""
import argparse
import time

import numpy as np

import bots
from bots.opp_model import EXPLOITS, ModelBot
from sim import gauntlet

FIELDS = ["call", "allin", "random", "nit", "lp", "tag", "lag", "foldbot", "riverrat", "tilt",
          "gtolite", "potodds", "mc", "bayes"]


def _register():
    for e in EXPLOITS:
        bots.REGISTRY[f"model-no-{e}"] = (lambda e: lambda name=None, seed=0: ModelBot(
            name, seed, exploits=EXPLOITS - {e}))(e)
    bots.REGISTRY["model-seat"] = lambda name=None, seed=0: ModelBot(name, seed, use_names=False)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hands", type=int, default=6000)
    ap.add_argument("--mixes", type=int, default=4)
    ap.add_argument("--variants", default="main,model," + ",".join(f"model-no-{e}" for e in sorted(EXPLOITS)))
    a = ap.parse_args(argv)
    _register()
    t = time.perf_counter()
    variants = a.variants.split(",")
    res = {v: gauntlet.gauntlet(v, a.hands, a.mixes, opponents=FIELDS) for v in variants}
    labels = [r[0][:12] for r in res[variants[0]]]
    print(f"{'variant':<20}" + "".join(f"{l:>12}" for l in labels) + f"{'MEAN':>9}")
    for v in variants:
        vals = [r[1] for r in res[v]]
        print(f"{v:<20}" + "".join(f"{x:>12.1f}" for x in vals) + f"{np.mean(vals):>9.1f}")
    print(f"done in {time.perf_counter() - t:.0f}s")


if __name__ == "__main__":
    main()
