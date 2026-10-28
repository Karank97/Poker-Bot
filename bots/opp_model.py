"""Opponent model and the exploitative bot built on it.

OpponentModel keeps per-opponent counts, keyed by bot name (identity may
persist across games) and by seat (fallback when names are hidden). Rates are
Beta-smoothed toward population priors, so a few hands do not swing decisions.
Each profile gets an archetype (nit, tag, lag, station, maniac, shover) with a
confidence = hands / (hands + CONF_HANDS).

ModelBot = MainBot + adjust() that turns the profiles into exploit knobs:
  shover / maniac  -> assume wide shove and bet ranges, call wider, trap strong hands
  calling station  -> value bet thinner, stop bluffing
  nit              -> steal their blinds, respect (fold to) their aggression
  folds to bets    -> bluff more, especially in position
"""
from dataclasses import dataclass, field

from core.cards import board_category, category, evaluate
from core.preflop import hand_percentile, strong_percentile
from core.state import AGGRESSIVE, CALL, FOLD, RAISE, position_names

from .main_bot import Adjust, MainBot

# (prior mean, prior weight in pseudo-observations)
PRIORS = {
    "vpip": (0.25, 8), "pfr": (0.15, 8), "threebet": (0.06, 6), "agg": (0.40, 6),
    "ftb": (0.45, 6), "allin": (0.02, 10), "sd_pct": (0.35, 3), "bluff": (0.20, 5),
}
CONF_HANDS = 25
STREETS_POST = ("flop", "turn", "river")


@dataclass
class Counts:
    hands: int = 0
    vpip: int = 0
    pfr: int = 0
    threebet: int = 0
    threebet_opp: int = 0
    aggr: int = 0           # postflop bets + raises
    passive: int = 0        # postflop calls + checks
    folds: dict = field(default_factory=lambda: {s: 0 for s in STREETS_POST})
    faced: dict = field(default_factory=lambda: {s: 0 for s in STREETS_POST})
    allin: int = 0
    shown: int = 0
    shown_pct: float = 0.0  # sum of preflop percentiles of shown hands
    aggr_sd: int = 0        # showdowns after betting / raising postflop
    aggr_sd_weak: int = 0   # ...holding a hand that did not improve on the board


@dataclass
class Profile:
    hands: int
    vpip: float
    pfr: float
    threebet: float
    agg: float              # aggression frequency postflop: aggr / (aggr + passive)
    ftb: float              # fold to a bet, all postflop streets
    ftb_street: dict
    allin: float
    sd_pct: float           # avg preflop percentile shown at showdown (0 = best)
    bluff: float            # share of postflop aggression shown down with air
    archetype: str
    conf: float


def _rate(x, n, key):
    m, w = PRIORS[key]
    return (x + m * w) / (n + w)


def classify(p: Profile) -> str:
    if p.allin > 0.25:
        return "shover"
    if p.pfr > 0.35 or (p.agg > 0.65 and p.vpip > 0.4):
        return "maniac"
    if p.vpip > 0.28 and (p.pfr < 0.3 * p.vpip or p.agg < 0.25):
        return "station"
    if p.vpip < 0.14:
        return "nit"
    if p.vpip > 0.28:
        return "lag"
    return "tag"


class OpponentModel:
    def __init__(self, me_name=None, use_names=True):
        self.me = me_name
        self.use_names = use_names
        self.by_key = {}

    # ------------------------------------------------------------ keys
    def _keys(self, seat, names):
        keys = [("seat", seat)]
        nm = names.get(seat)
        if self.use_names and nm is not None:
            keys.insert(0, ("name", nm))
        return keys

    def _counts(self, key):
        c = self.by_key.get(key)
        if c is None:
            c = self.by_key[key] = Counts()
        return c

    # ------------------------------------------------------------ update
    def update(self, result, my_seat=None):
        names = result.names
        hist = result.history
        seats = set(result.start_stacks)
        flags = {s: dict(vpip=False, pfr=False, tb=False, tbo=False, allin=False) for s in seats}
        committed = {s: 0 for s in seats}
        street, cur_bet, street_bet, raises = None, 0, {}, 0
        post = {s: dict(aggr=0, passive=0) for s in seats}
        folds = {s: {} for s in seats}
        faced = {s: {} for s in seats}
        for h in hist:
            s = h.seat
            if h.street != street:
                street, cur_bet, raises = h.street, 0, 0
                street_bet = {q: 0 for q in seats}
            if h.kind == "post":
                street_bet[s] = h.amount
                committed[s] += h.amount
                cur_bet = max(cur_bet, h.amount)
                continue
            facing = cur_bet > street_bet[s]
            if street == "preflop":
                if h.kind in (CALL, RAISE):
                    flags[s]["vpip"] = True
                if h.kind == RAISE:
                    flags[s]["pfr"] = True
                if raises >= 1 and not flags[s]["tbo"]:
                    flags[s]["tbo"] = True
                    flags[s]["tb"] = h.kind == RAISE
            else:
                if h.kind in AGGRESSIVE:
                    post[s]["aggr"] += 1
                elif h.kind in (CALL, "check"):
                    post[s]["passive"] += 1
                if facing:
                    faced[s][street] = faced[s].get(street, 0) + 1
                    if h.kind == FOLD:
                        folds[s][street] = folds[s].get(street, 0) + 1
            if h.kind == CALL:
                street_bet[s] += h.amount
                committed[s] += h.amount
            elif h.kind in AGGRESSIVE:
                committed[s] += h.amount - street_bet[s]
                street_bet[s] = h.amount
                cur_bet = h.amount
                raises += 1
                if committed[s] >= result.start_stacks[s]:
                    flags[s]["allin"] = True
        for s in seats:
            if s == my_seat or names.get(s) == self.me:
                continue
            for key in self._keys(s, names):
                c = self._counts(key)
                f = flags[s]
                c.hands += 1
                c.vpip += f["vpip"]
                c.pfr += f["pfr"]
                c.threebet_opp += f["tbo"]
                c.threebet += f["tb"]
                c.allin += f["allin"]
                c.aggr += post[s]["aggr"]
                c.passive += post[s]["passive"]
                for st in STREETS_POST:
                    c.faced[st] += faced[s].get(st, 0)
                    c.folds[st] += folds[s].get(st, 0)
                if s in result.showdown:
                    hole = result.showdown[s]
                    c.shown += 1
                    c.shown_pct += hand_percentile(hole)
                    if post[s]["aggr"] and len(result.board) == 5:
                        c.aggr_sd += 1
                        c.aggr_sd_weak += category(evaluate(list(hole) + result.board)) <= \
                            board_category(result.board)

    # ------------------------------------------------------------ query
    def profile(self, seat, names) -> Profile:
        c = None
        for key in self._keys(seat, names):
            c = self.by_key.get(key)
            if c is not None:
                break
        c = c or Counts()
        faced = sum(c.faced.values())
        p = Profile(
            hands=c.hands,
            vpip=_rate(c.vpip, c.hands, "vpip"),
            pfr=_rate(c.pfr, c.hands, "pfr"),
            threebet=_rate(c.threebet, c.threebet_opp, "threebet"),
            agg=_rate(c.aggr, c.aggr + c.passive, "agg"),
            ftb=_rate(sum(c.folds.values()), faced, "ftb"),
            ftb_street={st: _rate(c.folds[st], c.faced[st], "ftb") for st in STREETS_POST},
            allin=_rate(c.allin, c.hands, "allin"),
            sd_pct=(c.shown_pct + PRIORS["sd_pct"][0] * PRIORS["sd_pct"][1]) / (c.shown + PRIORS["sd_pct"][1]),
            bluff=_rate(c.aggr_sd_weak, c.aggr_sd, "bluff"),
            archetype="", conf=c.hands / (c.hands + CONF_HANDS))
        p.archetype = classify(p)
        return p


def _blend(neutral, target, conf):
    return neutral + (target - neutral) * conf


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


EXPLOITS = frozenset({"ranges", "bettor", "jam", "bluff", "value", "trap", "steal", "vs_raise"})


class ModelBot(MainBot):
    name = "model"

    def __init__(self, name=None, seed=0, config=None, use_names=True, exploits=EXPLOITS):
        super().__init__(name, seed, config)
        self.model = OpponentModel(self.name, use_names)
        self.exploits = set(exploits)

    def hand_over(self, result):
        me = next((s for s, n in result.names.items() if n == self.name), None)
        self.model.update(result, me)

    def profiles(self, state):
        return {s: self.model.profile(s, state.names) for s in state.live if s != state.my_seat}

    # ------------------------------------------------------------ adjust
    def adjust(self, state) -> Adjust:
        profs = self.profiles(state)
        adj = Adjust()
        if not profs:
            return adj
        on = self.exploits
        if "ranges" in on:
            adj.range_widths = {s: (_blend(0.18, _clamp(p.pfr * 1.1, 0.03, 1.0), p.conf),
                                    _blend(0.45, _clamp(p.vpip, 0.05, 1.0), p.conf))
                                for s, p in profs.items()}
        # the bettor / shover we are facing
        aggressor = state.last_aggressor() if state.to_call > 0 else None
        if aggressor in profs:
            p = profs[aggressor]
            factor = _clamp(p.bluff / PRIORS["bluff"][0], 0.3, 4.0)
            if p.archetype in ("maniac", "shover"):
                factor = max(factor, 2.5)
            if p.archetype == "station":
                factor = min(factor, 0.7)
            if "bettor" in on:
                if p.archetype == "nit":
                    factor = min(factor, 0.4)
                    adj.fold_to_aggr = 0.05 * p.conf
                adj.bettor_bluff = _blend(1.0, factor, p.conf)
            if "jam" in on and p.allin > 0.05:
                adj.jam_width = _clamp(_blend(0.12, p.allin * 1.3, p.conf), 0.01, 1.0)
            elif "jam" in on and p.conf > 0.5:
                # a tight player's re-shove range tracks how often they go all in at all
                adj.jam_width = _clamp(max(p.allin * 1.3, p.threebet * 0.5), 0.01, 0.12)
        # value and bluff vs the callers
        ftbs = [p.ftb for p in profs.values()]
        confs = [p.conf for p in profs.values()]
        conf = min(confs)
        fold_all = 1.0
        for f in ftbs:
            fold_all *= f
        stations = [p for p in profs.values() if p.archetype in ("station", "shover", "maniac")]
        if len(profs) == 1:
            p = next(iter(profs.values()))
            mult = _clamp((p.ftb / 0.45) ** 2, 0.15, 3.0)
            if self._in_position(state):
                mult *= 1.25
            if p.archetype == "station":
                mult = min(mult, 0.15)
            if "bluff" in on:
                adj.bluff_mult = _blend(1.0, mult, p.conf)
            if "trap" in on and p.agg > 0.6 and p.archetype in ("maniac", "shover", "lag"):
                adj.trap = 0.5 * p.conf
        if "value" not in on:
            return adj
        loose = sum(1 - f for f in ftbs) / len(ftbs)
        adj.caller_air = _blend(1.0, _clamp(loose / 0.55, 0.5, 3.0), conf)
        if stations and len(stations) == len(profs):
            adj.value_shift = 0.06 * conf
        elif fold_all > 0.5:
            adj.value_shift = -0.03 * conf
        return adj

    def _in_position(self, state):
        order = position_names(len(state.positions)) if state.positions else []
        post = [n for n in order if n in ("SB", "BB")] + [n for n in order if n not in ("SB", "BB")]
        live_pos = [state.positions.get(s) for s in state.live]
        mine = state.position
        return all(post.index(mine) >= post.index(x) for x in live_pos if x in post) if mine in post else False

    # ------------------------------------------------------------ preflop exploits
    def preflop(self, state, adj):
        facing = state.preflop_facing()
        profs = self.profiles(state)
        eff_bb = state.effective_stack / state.big_blind
        my_total = state.my_stack + state.my_street_bet
        if facing == "unopened" and eff_bb > 15 and "steal" in self.exploits:
            p_fold = self._fold_prob_behind(state, profs)
            if p_fold is not None:
                pct = hand_percentile(state.hole)
                if (p_fold >= 0.62) or (p_fold >= 0.45 and pct <= 0.60):
                    return self._raise_to(state, (2.2 + state.limpers()) * state.big_blind)
        if facing == "raise" and state.to_call < self.cfg.big_shove_frac * my_total and "vs_raise" in self.exploits:
            raiser = state.last_aggressor("preflop")
            p = profs.get(raiser)
            if p is not None and p.conf >= 0.3:
                sp = strong_percentile(state.hole)
                bb_bonus = 1.3 if state.position == "BB" else 1.0
                call_cut = _clamp(p.pfr * 0.6 * bb_bonus, 0.03, 0.35)
                three_cut = _clamp(p.pfr * 0.22, 0.015, 0.15)
                if sp <= three_cut:
                    target = (state.my_street_bet + state.to_call) * 3.2
                    if target >= 0.4 * my_total:
                        target = state.legal.max_raise_to
                    return self._raise_to(state, target)
                if sp <= call_cut:
                    return self._call(state)
                return self._passive(state)
        return super().preflop(state, adj)

    def _fold_prob_behind(self, state, profs):
        """Chance everyone still to act folds to an open, or None if unknown."""
        order = position_names(len(state.positions))
        if state.position not in order:
            return None
        behind = order[order.index(state.position) + 1:]
        seats = [s for s, pos in state.positions.items() if pos in behind and s in profs]
        if not seats or min(profs[s].conf for s in seats) < 0.4:
            return None
        pf = 1.0
        for s in seats:
            p = profs[s]
            cont = _clamp(p.vpip * 0.7, 0.02, 0.95)     # continue vs a raise ~ 70% of VPIP
            pf *= 1 - cont
        return pf
