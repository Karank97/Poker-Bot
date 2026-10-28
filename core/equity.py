"""Monte Carlo equity, anytime: stops at `samples` or when `time_budget` runs out.

Equity is our share of the pot at showdown against `n_opps` opponents holding
random cards (ties split). `opp_weight(c1, c2) -> keep probability` optionally
narrows opponent hands by rejection sampling (e.g. "they called a raise, so
they hold roughly the top 30%"). `opp_range`, a list of (c1, c2) combos, draws
every opponent hand exactly from that range instead.
"""
import time

import numpy as np

from .cards import FULL_DECK, native_cards, native_evaluate

_NATIVE = native_cards()
_EVAL = native_evaluate()
_BATCH = 32


def equity_detailed(hole, board=(), n_opps=1, samples=1000, time_budget=None,
                    rng=None, dead=(), opp_weight=None, min_samples=16,
                    opp_range=None):
    """Return (equity, samples_used). time_budget is in seconds."""
    if n_opps < 1:
        return 1.0, 0
    start = time.perf_counter()
    rng = rng if rng is not None else np.random.default_rng()
    used = set(hole) | set(board) | set(dead)
    deck = np.array([c for c in FULL_DECK if c not in used], dtype=np.int64)
    if opp_range is not None:
        return _equity_vs_range(hole, board, n_opps, samples, time_budget, rng,
                                used, opp_range, start, min_samples)
    n_board = 5 - len(board)
    # with rejection sampling we draw spare cards for redraws
    spare = 16 if opp_weight is not None else 0
    need = n_board + 2 * n_opps + spare
    if need > len(deck):
        need = len(deck)
        spare = need - n_board - 2 * n_opps
    nat = _NATIVE
    ev = _EVAL
    mine = [nat[c] for c in hole]
    known_board = [nat[c] for c in board]
    total = 0.0
    done = 0
    while done < samples:
        k = min(_BATCH, samples - done)
        draws = rng.random((k, len(deck))).argpartition(need - 1, axis=1)[:, :need]
        draws = deck[draws].tolist()
        for d in draws:
            b = known_board + [nat[c] for c in d[:n_board]]
            my_score = ev(mine + b)
            best_opp = -1
            ties = 0
            pos = n_board
            extra = d[n_board + 2 * n_opps:]
            for _ in range(n_opps):
                c1, c2 = d[pos], d[pos + 1]
                pos += 2
                if opp_weight is not None:
                    tries = 0
                    while extra and len(extra) >= 2 and tries < 8 and rng.random() > opp_weight(c1, c2):
                        c1, c2 = extra.pop(), extra.pop()
                        tries += 1
                s = ev([nat[c1], nat[c2]] + b)
                if s > best_opp:
                    best_opp, ties = s, 0
                elif s == best_opp:
                    ties += 1
            if my_score > best_opp:
                total += 1.0
            elif my_score == best_opp:
                total += 1.0 / (ties + 2)
        done += k
        if time_budget is not None and done >= min_samples and time.perf_counter() - start >= time_budget:
            break
    return total / done, done


def _equity_vs_range(hole, board, n_opps, samples, time_budget, rng, used,
                     opp_range, start, min_samples):
    combos = [c for c in opp_range if c[0] not in used and c[1] not in used]
    if not combos:
        raise ValueError("opponent range is empty after removing known cards")
    nat, ev = _NATIVE, _EVAL
    mine = [nat[c] for c in hole]
    known_board = [nat[c] for c in board]
    n_board = 5 - len(board)
    total, done = 0.0, 0
    while done < samples:
        k = min(_BATCH, samples - done)
        picks = rng.integers(0, len(combos), size=(k, n_opps * 4)).tolist()
        perms = rng.random((k, 52)).argsort(axis=1).tolist()  # shuffled decks
        for row, pk in enumerate(picks):
            taken = set(used)
            hands = []
            for idx in pk:
                c1, c2 = combos[idx]
                if c1 in taken or c2 in taken:
                    continue
                taken.add(c1)
                taken.add(c2)
                hands.append((c1, c2))
                if len(hands) == n_opps:
                    break
            while len(hands) < n_opps:  # rare: range collisions, fall back to random
                free = [c for c in FULL_DECK if c not in taken]
                c1, c2 = (free[i] for i in rng.choice(len(free), 2, replace=False))
                taken.update((c1, c2))
                hands.append((c1, c2))
            b = list(known_board)
            for c in perms[row]:
                if len(b) == 5:
                    break
                if c not in taken:
                    taken.add(c)
                    b.append(nat[c])
            my_score = ev(mine + b)
            best_opp, ties = -1, 0
            for c1, c2 in hands:
                s = ev([nat[c1], nat[c2]] + b)
                if s > best_opp:
                    best_opp, ties = s, 0
                elif s == best_opp:
                    ties += 1
            if my_score > best_opp:
                total += 1.0
            elif my_score == best_opp:
                total += 1.0 / (ties + 2)
        done += k
        if time_budget is not None and done >= min_samples and time.perf_counter() - start >= time_budget:
            break
    return total / done, done


def equity(hole, board=(), n_opps=1, samples=1000, time_budget=None, rng=None,
           dead=(), opp_weight=None, opp_range=None):
    return equity_detailed(hole, board, n_opps, samples, time_budget, rng, dead,
                           opp_weight, opp_range=opp_range)[0]
