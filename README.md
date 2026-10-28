# No Limit pokerbot prep

Prep repo for the "No Limit" pokerbot tournament (8-max NLHE, 50/100 blinds,
10,000 games). Goal: a strong, crash-proof bot running in well under 60 minutes
on event day. Only the adapter is rewritten on the day.

```
pip install -r requirements.txt
python -m pytest -q
```

## Layout

| Path | What |
|---|---|
| `core/cards.py` | Card ints (0..51), parsing (`As`, `10h`, `A♠`, `AsKd`), 169 hand classes, evaluator wrapper (eval7, falls back to treys; higher is better) |
| `core/equity.py` | Anytime Monte Carlo equity vs N random opponents, an optional soft weight, or an exact combo range |
| `core/preflop.py` | Cached 169-class equity table, position-keyed chart (UTG..BB), push/fold ranges |
| `core/state.py` | Protocol-agnostic `GameState`, `Legal`, `Action`, position naming |
| `core/data/preflop_equity.json` | Precomputed table. Rebuild: `python -m core.preflop --samples 20000` (~1 min on 4 cores) |

## Defaults and conventions (chosen, not confirmed)

- `Action.amount` for bet/raise is the **total street bet ("raise to")**; for call it is the chips added. The adapter converts if the dealer uses another convention.
- Positions in 8-max: `UTG, UTG1, MP, HJ, CO, BTN, SB, BB`; short-handed tables drop names from the front (UTG first). Heads-up the button is the SB.
- Preflop ranges are "top X% of combos". Opening ranking = mean of heads-up and 3-way equity ranks; facing a raise the ranking is heads-up equity vs a top-15% range (puts AK ahead of small pairs).
- Opening sizes per position live in `OPEN_PCT`, `THREEBET_PCT`, `CALL_PCT`, `FOURBET_PCT`, `CALL3_PCT` in `core/preflop.py`. Hands within `MIX_BAND` of a cutoff are mixed.
- Push/fold below `PUSHFOLD_BB = 15` effective big blinds.
- Equity speed: ~7 µs/sample heads-up, ~12 µs with 7 opponents (eval7), so ~2,000-3,000 samples fit in 15 ms.

See `RULES_NOTES.md` for what is assumed about the official rules.
