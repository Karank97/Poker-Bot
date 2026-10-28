"""Run a match and report bb/100 with a 95% confidence interval.

  python -m sim.run main call allin random fold raise call random --hands 10000
  python -m sim.run main nit --hands 10000 --duplicate
  python -m sim.run main call --persist --stack 1500   # push/fold depth

Duplicate mode plays each deal once per seat rotation (n rotations for n bots)
so every bot gets every seat's cards; --hands counts total hands played.
The CI is computed over deal blocks, which removes most of the card luck.
"""
import argparse
import math
import time

import numpy as np

from bots import make_bot
from sim.engine import Table


def unique_names(kinds):
    seen, out = {}, []
    for k in kinds:
        seen[k] = seen.get(k, 0) + 1
        out.append(k if kinds.count(k) == 1 else f"{k}#{seen[k]}")
    return out


def run_match(bots, hands=10000, duplicate=False, seed=0, persist=False, stack=10000,
              small_blind=50, big_blind=100):
    """Returns (summary rows, table). Each row: name, hands, bb100, ci95, stats."""
    n = len(bots)
    table = Table(bots, small_blind, big_blind, stack, persist, seed)
    blocks = {b.name: [] for b in bots}
    played = 0
    deal = 0
    rng = np.random.default_rng(seed)
    while played < hands:
        deck = rng.permutation(52).tolist()
        button = deal % n
        totals = {b.name: 0 for b in bots}
        rotations = range(n) if duplicate else range(1)
        for r in rotations:
            if duplicate:
                # bot i sits in seat (i + r) % n; cards stay with the seat
                seated = [None] * n
                for i, b in enumerate(bots):
                    seated[(i + r) % n] = b
                table.seat_bots(seated)
            for name, chips in table.play_hand(button, deck).items():
                totals[name] += chips
            played += 1
        for name, chips in totals.items():
            blocks[name].append(chips)
        deal += 1
    per_block = n if duplicate else 1
    rows = []
    for b in bots:
        x = np.array(blocks[b.name], dtype=float) / big_blind / per_block * 100
        mean = float(x.mean())
        ci = 1.96 * float(x.std(ddof=1)) / math.sqrt(len(x)) if len(x) > 1 else float("nan")
        rows.append((b.name, table.stats[b.name].hands, mean, ci, table.stats[b.name]))
    rows.sort(key=lambda r: -r[2])
    return rows, table


def print_rows(rows, title=""):
    if title:
        print(title)
    print(f"{'bot':<14}{'hands':>7}{'bb/100':>10}{'±95%':>9}{'err':>5}{'illeg':>6}{'avg ms':>8}{'max ms':>8}")
    for name, hands, mean, ci, st in rows:
        avg = st.think_time / max(1, st.decisions) * 1000
        print(f"{name:<14}{hands:>7}{mean:>10.1f}{ci:>9.1f}{st.errors:>5}{st.illegal:>6}"
              f"{avg:>8.2f}{st.max_think * 1000:>8.1f}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("bots", nargs="+", help="bot kinds from bots.REGISTRY")
    ap.add_argument("--hands", type=int, default=10000)
    ap.add_argument("--duplicate", action="store_true")
    ap.add_argument("--persist", action="store_true", help="carry stacks between hands (rebuy on bust)")
    ap.add_argument("--stack", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    names = unique_names(a.bots)
    bots = [make_bot(k, name, seed=a.seed * 100 + i) for i, (k, name) in enumerate(zip(a.bots, names))]
    t = time.perf_counter()
    rows, _ = run_match(bots, a.hands, a.duplicate, a.seed, a.persist, a.stack)
    print_rows(rows)
    print(f"{a.hands} hands in {time.perf_counter() - t:.1f}s")


if __name__ == "__main__":
    main()
