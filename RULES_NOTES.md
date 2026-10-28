# Rules notes

## Fetch attempt (2026-10-28)

`https://pokerbot.sequoiacap.com/rules.html` and the site root could **not** be
fetched from the build environment: the host is not on this session's network
allowlist (proxy refused the tunnel). Nothing below is confirmed against the
official rules. To verify, allow `pokerbot.sequoiacap.com` in the environment's
network settings, or open the page yourself and update this file.

## Working assumptions (from the brief and the public landing page)

| Topic | Assumption | Where it is configurable |
|---|---|---|
| Format | 8 bots per table, NLHE, blinds 50/100 | sim config, `GameState.big_blind` |
| Volume | 10,000 games vs the field | sim `--hands` |
| Stacks | Reset to 10,000 (100bb) each hand heads-up; 8-max table shows varying stacks, so persistence is unknown | sim flag for stack persistence (Phase 2); push/fold under 15bb (`core.preflop.PUSHFOLD_BB`) |
| Protocol | `DEAL <cards>`, `FLOP/TURN/RIVER <cards>`, `ACT? <legal actions>`; reply with one action like `CALL 50` / `CHECK` | adapter (Phase 7) |
| Raise amount semantics | Unknown: "raise to" vs "raise by" | adapter TODO; internally always "raise to" |
| Time budget | Observed avg think time 9-22 ms; default 15 ms/decision | Phase 6 config |
| Language | Python allowed | - |
| Helper code / AI assistance | Allowed | - |
| Opponent identity | May persist across games; track by seat and by name | opponent model (Phase 5) |

## Protocol details still unknown (all are config flags or TODO(day) in adapter/template.py)

- Whether opponent actions, pot, stacks, button / seat, names and showdown cards are broadcast, and in what format.
- "Raise to" vs "raise by", for our replies and for the numbers on `ACT?` lines.
- Whether `CALL` must carry the amount (observed: `CALL 50`), and whether ALL-IN is a separate verb.
- Handshake / authentication on connect.
- Whether the process persists between games (opponent model memory).

## Contradictions found

None checked yet (page unreachable).
