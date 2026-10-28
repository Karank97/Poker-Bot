"""Gauntlet: one bot against every zoo bot and against mixed fields.

  python -m sim.gauntlet main --hands 4000
  python -m sim.gauntlet main --vs base --hands 4000     # A/B two bots (ablation)

"vs <zoo>" seats the bot with 7 copies of that zoo bot (8-handed, like the
event) in duplicate mode. "mix k" seats it with 7 distinct zoo bots drawn with
seed k. Runs in parallel across CPU cores.
"""
import argparse
import time
from multiprocessing import Pool

import numpy as np

from bots import ZOO, make_bot
from sim.run import run_match

MIX_POOL = [k for k in ZOO if k != "fold"]


def _field(spec):
    kind, arg = spec
    if kind == "vs":
        return [arg] * 7
    rng = np.random.default_rng(arg)
    return list(rng.choice(MIX_POOL, 7, replace=False))


def _job(args):
    hero, spec, hands, seed, persist, stack = args
    field = _field(spec)
    bots = [make_bot(hero, "HERO", seed=seed)] + [
        make_bot(k, f"{k}#{i}", seed=seed * 31 + i) for i, k in enumerate(field)]
    rows, _ = run_match(bots, hands, duplicate=True, seed=seed, persist=persist, stack=stack)
    me = next(r for r in rows if r[0] == "HERO")
    label = f"vs {spec[1]}" if spec[0] == "vs" else f"mix {spec[1]}: " + ",".join(field)
    st = me[4]
    return label, me[2], me[3], st.think_time / max(1, st.decisions) * 1000, st.max_think * 1000, st.errors + st.illegal


def gauntlet(hero, hands=4000, mixes=4, seed=1, persist=False, stack=10000, processes=None, opponents=None):
    specs = [("vs", z) for z in (opponents or MIX_POOL)] + [("mix", seed + k) for k in range(mixes)]
    jobs = [(hero, sp, hands, seed, persist, stack) for sp in specs]
    with Pool(processes) as pool:
        return pool.map(_job, jobs)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("hero")
    ap.add_argument("--vs", help="second hero for an A/B comparison")
    ap.add_argument("--hands", type=int, default=4000)
    ap.add_argument("--mixes", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--persist", action="store_true")
    ap.add_argument("--stack", type=int, default=10000)
    a = ap.parse_args(argv)
    t = time.perf_counter()
    heroes = [a.hero] + ([a.vs] if a.vs else [])
    res = {h: gauntlet(h, a.hands, a.mixes, a.seed, a.persist, a.stack) for h in heroes}
    base = res[heroes[0]]
    head = f"{'field':<34}" + "".join(f"{h + ' bb/100':>16}{'±95%':>7}" for h in heroes)
    print(head + (f"{'diff':>8}" if a.vs else "") + f"{'avg ms':>8}{'max ms':>8}{'err':>5}")
    for i, row in enumerate(base):
        line = f"{row[0][:34]:<34}"
        for h in heroes:
            r = res[h][i]
            line += f"{r[1]:>16.1f}{r[2]:>7.1f}"
        if a.vs:
            line += f"{res[heroes[0]][i][1] - res[heroes[1]][i][1]:>8.1f}"
        line += f"{row[3]:>8.2f}{row[4]:>8.1f}{row[5]:>5}"
        print(line)
    for h in heroes:
        print(f"{h}: mean over fields {np.mean([r[1] for r in res[h]]):.1f} bb/100, "
              f"beats {sum(r[1] > 0 for r in res[h])}/{len(res[h])}")
    print(f"done in {time.perf_counter() - t:.0f}s")


if __name__ == "__main__":
    main()
