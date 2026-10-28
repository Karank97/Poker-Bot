# Event day: 60-minute plan

Rule zero: **something legal must be answering the dealer at all times.** The fold/check fallback goes in first, and the strong bot is wired in only after that.

Everything you edit lives in `adapter/template.py`. Search it for `TODO(day)`.

## Before the clock starts

- `pip install -r requirements.txt`, then run `make test` and `make bot`. All green means the toolchain works on this laptop.
- Open three terminals:
  1. The adapter.
  2. `tail -f logs/raw.log` (every line in and out).
  3. `tail -f logs/hands.log`.
- Close everything CPU-heavy. Decisions spike past 15 ms when other processes compete for the cores.

## Minutes 0-5: read the rules and verify (checklist)

Write each answer into the `Config` block of `adapter/template.py` as you go.

- [ ] **Language / runtime:** is Python allowed, which version, and can we `pip install` eval7 and numpy? If eval7 won't install, the bundle falls back to the vendored treys (slower, about 3x fewer samples).
- [ ] **Transport:**
  - stdin/stdout → `python main.py`.
  - TCP text → `--tcp HOST PORT`.
  - JSON → `--jsonl HOST PORT`, then fix `json_to_line` / `line_to_json`.
  - Is a handshake or token needed? Set `Config.HELLO`.
- [ ] **Time limit per action:** set `Config.BUDGET`. We use 60% of it ourselves. Does the dealer clock include network time? If yes, lower `BUDGET`. What happens on a timeout: auto-fold, or disqualification?
- [ ] **Action format:**
  - Exact reply strings: `CALL 50` vs `CALL`? → `CALL_WITH_AMOUNT`.
  - Case → `REPLY_UPPERCASE`.
  - Is ALL-IN its own verb?
- [ ] **Raise amounts:** does `RAISE 300` mean "to 300" or "by 300"? Set `RAISE_SEMANTICS`. Do the same for the numbers on `ACT?` lines (`LEGAL_SEMANTICS`) and on opponent action lines (`ACTION_SEMANTICS`).
- [ ] **Stack depth:** does every hand start at 10,000, or do stacks carry over (`PERSIST_STACKS`)? Below 15bb the bot switches to push/fold automatically.
- [ ] **What the dealer tells us:** opponent actions? pot size? stacks? button and seat? showdown cards? hand end and result? Map each one to the ASSUMED handlers in `Protocol.event` (`ACTION`, `POT`, `STACKS`, `BUTTON`, `SEAT`, `PLAYERS`, `SHOWDOWN`, `END`). Rename the message heads to match the real ones. Missing ones are fine: the bot infers what it can.
- [ ] **Own actions:** does the dealer echo our own action back? → `ECHOES_OWN_ACTIONS`.
- [ ] **Persistence:**
  - Is the process restarted between games? If yes, the opponent model starts empty each game, which is still OK because the priors are sane. Consider saving and loading the model.
  - Is there chat? Ignore it.
- [ ] **Opponent identity:** are bot names visible, and are they stable across games? If yes, `PLAYERS` (or names in `ACTION` lines) lets the model carry reads across games. If not, it falls back to seat stats.
- [ ] **Number of players and blinds:** set `NUM_PLAYERS` and `BLINDS`. Blinds can also come from a `BLINDS` message.

## Minutes 5-10: plumbing with the fallback bot

1. Edit the transport and message names in `adapter/template.py`.
2. In `make_bot()`, temporarily return `SafeBot(Bot())`. The base `Bot` only checks or folds.
3. Connect to the practice dealer. In `logs/raw.log`, confirm that every `ACT?` gets exactly one reply, with a verb from that line.
4. If the dealer rejects a reply, the fix is in `serialize()` or the config flags, not in the bot.

**Checkpoint at 10 min:** check/fold is running against the real dealer with no rejected replies.

## Minutes 10-25: wire in the main bot

1. Restore `make_bot()` to `SafeBot(ModelBot(...))` (the default).
2. Watch `logs/hands.log`. Hole cards and board must parse correctly, and our position should not always be `MP`. If it is, send or parse `BUTTON` / `SEAT`, or accept the inference (`BB` / `SB` from the price, `DEFAULT_POSITION` otherwise).
3. Run with `--debug` for a minute. The stderr line shows equity, pot odds, SPR and EV, so sanity-check them. AA preflop should have equity around 0.85 heads-up, and the pot should match the dealer's pot.
4. Check the pot: if the dealer sends no `POT`, our estimate comes from the actions we see. If it's badly wrong, pot odds are wrong: add the real pot message.
5. Rebuild and rerun locally: `make bot` (bundle plus fake-dealer smoke test, both `--minimal` and full).

**Checkpoint at 25 min:** the model bot is playing the real dealer with no timeouts and no ERR lines in `raw.log`.

## Minutes 25-50: tune (only with evidence)

- **Read the field.** `hands.log` showdowns and `raw.log` actions show what the opponents are. Many shovers? Many folders?
- **Local A/B:** `python -m sim.gauntlet model --vs main --hands 3000` for a quick comparison, or add a zoo bot that mimics what you see.
- **Safe knobs** (`bots/main_bot.py: Config`):
  - `open_bb`
  - `value_th`
  - `cbet_bluff` (turn it down against stations)
  - `call_margin`
  - `jam_range`
- **Timing:** if timeouts appear, lower `Config.BUDGET` or `MainBot.Config.equity_samples`.
- **No rewrites.** Every change is one number, followed by `make test`.

## Minutes 50-60: freeze

- [ ] `make test` and `make bot` are green.
- [ ] `git commit -am "event day freeze"`.
- [ ] Submit `dist/bot` (entry point `python main.py ...`).
- [ ] Connect the frozen build and watch `raw.log` for two minutes: replies on time, no ERR lines.
- [ ] Do not touch it again unless it's broken. If it breaks, go back to the last commit, not forward.

## If things go wrong

| Symptom | Fix |
|---|---|
| Dealer rejects our reply | `CALL_WITH_AMOUNT`, `REPLY_UPPERCASE`, `RAISE_SEMANTICS`; read the rejected line in `raw.log` |
| Timeouts | lower `BUDGET`; set `HARD_TIMEOUT = True`; check nothing else is using the CPU |
| ERR lines in `raw.log` | the reply was still sent (emergency CHECK/FOLD). Fix the parser for that message |
| Bot folds everything | position or pot wrong? Run `--debug`. If `ACT?` shows CALL with no ACTION info, check `infer_hidden_bet` |
| eval7 won't install | nothing to do: the treys fallback is vendored in `dist/bot/vendor` |
