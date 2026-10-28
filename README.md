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
| `core/bot.py` | `Bot` base class (`act(state) -> Action`, `hand_over(result)`) |
| `sim/engine.py` | 2-8 seat NLHE engine: blinds, min-raise and reopen rules, side and split pots, stack persistence with auto-rebuy, error/illegal counting |
| `sim/run.py` | Match runner: bb/100 with 95% CI, duplicate mode, think-time stats |
| `bots/__init__.py` | Bot registry (`make_bot`) |
| `bots/zoo/` | Reference opponents (see below) |
| `core/data/preflop_equity.json` | Precomputed table. Rebuild: `python -m core.preflop --samples 20000` (~1 min on 4 cores) |

## Defaults and conventions (chosen, not confirmed)

- `Action.amount` for bet/raise is the **total street bet ("raise to")**; for call it is the chips added. The adapter converts if the dealer uses another convention.
- Positions in 8-max: `UTG, UTG1, MP, HJ, CO, BTN, SB, BB`; short-handed tables drop names from the front (UTG first). Heads-up the button is the SB.
- Preflop ranges are "top X% of combos". Opening ranking = mean of heads-up and 3-way equity ranks; facing a raise the ranking is heads-up equity vs a top-15% range (puts AK ahead of small pairs).
- Opening sizes per position live in `OPEN_PCT`, `THREEBET_PCT`, `CALL_PCT`, `FOURBET_PCT`, `CALL3_PCT` in `core/preflop.py`. Hands within `MIX_BAND` of a cutoff are mixed.
- Push/fold below `PUSHFOLD_BB = 15` effective big blinds.
- Equity speed: ~7 µs/sample heads-up, ~12 µs with 7 opponents (eval7), so ~2,000-3,000 samples fit in 15 ms.

## Main bot (`bots/main_bot.py`)

- Preflop: position chart with mixed frequencies (open 2.5bb +1bb per limper; 3-bet 3x in position, 3.8x out of position; 4-bet 2.3x, jam when that commits 40% of the stack). Push/fold at or below 15bb effective. Facing a shove or a raise of 35%+ of our stack, it calls only when its equity against the raiser's assumed range (top 12% / 4% / 2% for 1 / 2 / 3+ raises) beats the all-in price.
- Postflop: anytime Monte Carlo equity (6 ms total budget, at most 800 samples). Opponent hands are narrowed by their preflop action (raiser top 18%, caller 35-55%, blind 75%). When an opponent bets, hands that don't improve on the board are down-weighted (bluff share 30% / 20% / 10% on flop / turn / river).
- Value bets need equity >= 0.62 / 0.68 / 0.74 (heads-up / 3-way / 4+-way) **and** >= 52% against the range that would call. Sizes are 50% or 75% pot, plus a shove when SPR < 1.6. It bluffs only heads-up (flop c-bet 40%, turn 18%, river 10%).
- Calls need pot odds + 4% (+6% per extra raise, +2% per extra opponent).
- Every knob is in `Config`. The exploit hooks are `Adjust` / `MainBot.adjust(state)`.
- Gauntlet: `python -m sim.gauntlet main --hands 8000` (7 copies of each zoo bot, 8-handed, duplicate mode, plus mixed fields).

## Opponent model (`bots/opp_model.py`, registry name `model`)

- Per opponent, keyed by **bot name** (when the dealer shows names) and by **seat** (fallback): VPIP, PFR, 3-bet, postflop aggression frequency, fold-to-bet by street, all-in frequency, preflop strength of shown hands, and "bluff rate" (share of postflop aggression shown down with a hand that didn't improve on the board).
- Rates are Beta-smoothed toward population priors (`PRIORS`, 3-10 pseudo-hands). Confidence = hands / (hands + 25); every exploit is blended from neutral by confidence.
- Archetypes: `shover` (all-in > 25%), `maniac` (PFR > 35%), `station` (VPIP > 28% with PFR < 0.3 x VPIP), `nit` (VPIP < 14%), `lag`, `tag`.
- Exploits (each can be switched off for ablation: `ModelBot(exploits=EXPLOITS - {"steal"})`):
  - `ranges`: preflop raise and call ranges from the opponent's PFR / VPIP.
  - `bettor`: bluff share of a bettor's range from their measured bluff rate (maniac/shover at least 2.5x, nit at most 0.4x plus an extra call margin).
  - `jam`: a shover's range = their all-in frequency.
  - `bluff`: heads-up bluff frequency scales with (fold-to-bet / 0.45)^2, x1.25 in position; about 0 vs stations.
  - `value`: thinner value vs stations (callers hold more air).
  - `trap`: slowplay strong hands vs aggressive players.
  - `steal`: open any hand when the players behind fold to a raise often enough (>= 62%), top 60% when >= 45%.
  - `vs_raise`: defend / 3-bet ranges sized to the raiser's PFR.
- Ablation: `python -m sim.ablation --hands 6000`.

## Opponent zoo (`bots/zoo/`, registry names in brackets)

| Bot | Style |
|---|---|
| `fold` | check/fold only |
| `call` | always call/check (calling station) |
| `raise` | always min-raise preflop, pot-bet postflop |
| `allin` | shove every hand |
| `random` | uniform fold / call / random-size raise |
| `nit` | top 10% only, folds to aggression without a strong hand |
| `lp` | loose-passive: limps/calls ~55%, rarely bets |
| `tag` | tight-aggressive, ~20% VPIP |
| `lag` | loose-aggressive, ~38% VPIP, bluffs a lot |
| `foldbot` | FOLDBOT: plays ~2.5% of hands |
| `riverrat` | RIVERRAT: loose preflop, never folds postflop |
| `tilt` | TILT: LAG that widens and bluffs more as it loses (full tilt at -100bb over 30 hands) |
| `gtolite` | GTO-lite TAG: chart preflop, bluff ratio from bet size, MDF defence |
| `potodds` | basic equity bot: calls when equity beats pot odds |
| `mc` | Monte Carlo equity (200 samples) + EV of fold / call / two raise sizes |
| `bayes` | adaptive: Beta-smoothed per-opponent aggression and fold-to-bet |

Most style bots share one `Parametric` class (`bots/zoo/styles.py`), so a new archetype is a few numbers.

## Simulator

```
python -m sim.run call allin random fold raise call random fold --hands 10000
python -m sim.run call random --hands 4000 --duplicate
python -m sim.run call allin random raise --persist --stack 1500
```

- Stacks reset to `--stack` (default 10,000) every hand unless `--persist`; with `--persist` a busted bot rebuys to the starting stack (counted in `SeatStats.rebuys`).
- `--duplicate` replays each deal once per seat rotation, so every bot gets every seat's cards. The CI is computed over deal blocks.
- Engine rules: an all-in raise smaller than a full raise does not reopen betting for players who already acted; odd chips in split pots go to the first winner left of the button; uncalled bets are returned.
- A bot's exception becomes check/fold; an illegal action is mapped to the nearest legal one (an out-of-range raise is clamped, an illegal check becomes a fold). Both are counted.

See `RULES_NOTES.md` for what is assumed about the official rules.
