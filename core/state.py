"""Protocol-agnostic game state and action types.

The adapter's only job is to fill a GameState from dealer messages and turn an
Action back into the dealer's wire format. Amount convention inside this repo:
`Action.amount` for bet/raise is the TOTAL this street ("raise to"), and for
call it is the chips added. The adapter converts if the dealer differs.
"""
from dataclasses import dataclass, field

STREETS = ("preflop", "flop", "turn", "river")

FOLD, CHECK, CALL, BET, RAISE = "fold", "check", "call", "bet", "raise"
AGGRESSIVE = (BET, RAISE)

# Position names by table size, in preflop acting order (first to act first).
_NON_BLIND = ("UTG", "UTG1", "MP", "HJ", "CO", "BTN")


def position_names(n: int) -> list[str]:
    """Names in preflop acting order for an n-handed table (2..8).
    Heads-up: the button is the small blind and acts first preflop."""
    if n == 2:
        return ["SB", "BB"]
    if n < 2 or n > 8:
        raise ValueError("table size must be 2..8")
    return list(_NON_BLIND[-(n - 2):]) + ["SB", "BB"]


def seat_positions(seats: list, button) -> dict:
    """Map seat -> position name. `seats` are the seated players in clockwise
    order and `button` is the button's seat."""
    n = len(seats)
    b = seats.index(button)
    sb = b if n == 2 else (b + 1) % n
    order = [seats[(sb + 2 + i) % n] for i in range(n - 2)] + [seats[sb], seats[(sb + 1) % n]]
    return dict(zip(order, position_names(n)))


@dataclass(frozen=True)
class Action:
    kind: str           # fold / check / call / bet / raise
    amount: int = 0     # call: chips added; bet/raise: total street bet ("to")

    def __str__(self):
        return self.kind.upper() + (f" {self.amount}" if self.kind in (CALL, BET, RAISE) else "")


@dataclass
class Legal:
    """What the dealer allows right now. Raise bounds are 'raise to' totals."""
    can_fold: bool = True
    can_check: bool = False
    call_amount: int = 0            # 0 means call is not available
    can_raise: bool = False         # bet or raise
    min_raise_to: int = 0
    max_raise_to: int = 0
    raise_kind: str = RAISE         # BET when nobody has bet this street

    def actions(self) -> list[str]:
        out = []
        if self.can_check:
            out.append(CHECK)
        if self.call_amount > 0:
            out.append(CALL)
        if self.can_raise:
            out.append(self.raise_kind)
        if self.can_fold:
            out.append(FOLD)
        return out


@dataclass
class HistoryEntry:
    seat: object
    street: str
    kind: str
    amount: int = 0     # total street bet after the action for bet/raise


@dataclass
class GameState:
    hole: list = field(default_factory=list)        # ints (core.cards)
    board: list = field(default_factory=list)
    street: str = "preflop"
    pot: int = 0                    # all chips in the middle incl. this street
    to_call: int = 0
    min_raise_to: int = 0
    max_raise_to: int = 0
    my_seat: object = 0
    my_stack: int = 0               # chips behind (not yet committed)
    my_street_bet: int = 0          # chips already put in this street
    big_blind: int = 100
    small_blind: int = 50
    stacks: dict = field(default_factory=dict)      # seat -> chips behind
    positions: dict = field(default_factory=dict)   # seat -> 'UTG'.. 'BB'
    names: dict = field(default_factory=dict)       # seat -> bot name, if known
    live: list = field(default_factory=list)        # seats still in the hand (incl. me)
    history: list = field(default_factory=list)     # HistoryEntry, this hand
    legal: Legal = field(default_factory=Legal)
    hand_id: int = 0
    button: object = None

    # ------------------------------------------------------------ helpers
    @property
    def position(self) -> str:
        return self.positions.get(self.my_seat, "MP")

    @property
    def n_live_opponents(self) -> int:
        return max(0, len([s for s in self.live if s != self.my_seat]))

    @property
    def pot_odds(self) -> float:
        """Share of the final pot we must contribute to call."""
        if self.to_call <= 0:
            return 0.0
        return self.to_call / (self.pot + self.to_call)

    @property
    def effective_stack(self) -> int:
        """Smallest of our stack and the largest live opponent stack, in chips
        (including chips already committed this street)."""
        opp = [self.stacks.get(s, 0) for s in self.live if s != self.my_seat]
        mine = self.my_stack + self.my_street_bet
        return min(mine, max(opp) if opp else mine)

    @property
    def spr(self) -> float:
        return self.effective_stack / max(1, self.pot)

    def street_actions(self, street=None) -> list:
        s = street or self.street
        return [h for h in self.history if h.street == s]

    def raises_this_street(self) -> int:
        return sum(1 for h in self.street_actions() if h.kind in AGGRESSIVE)

    def preflop_facing(self) -> str:
        """'unopened', 'raise' or '3bet' from the preflop history."""
        n = sum(1 for h in self.street_actions("preflop") if h.kind in AGGRESSIVE)
        return ("unopened", "raise")[n] if n < 2 else "3bet"

    def limpers(self) -> int:
        return sum(1 for h in self.street_actions("preflop") if h.kind == CALL)

    def last_aggressor(self, street=None):
        for h in reversed(self.street_actions(street)):
            if h.kind in AGGRESSIVE:
                return h.seat
        return None

    def players_behind(self) -> int:
        """Live players acting after us this street (approx: by position)."""
        order = list(self.positions.values())
        names = position_names(len(order)) if order else []
        if self.street != "preflop" and names:
            # postflop order: SB, BB, then UTG..BTN
            names = [n for n in names if n in ("SB", "BB")] + [n for n in names if n not in ("SB", "BB")]
        me = self.position
        if me not in names:
            return self.n_live_opponents
        idx = names.index(me)
        live_pos = {self.positions.get(s) for s in self.live if s != self.my_seat}
        return sum(1 for n in names[idx + 1:] if n in live_pos)


@dataclass
class HandResult:
    """Sent to every bot when a hand ends (for opponent modelling)."""
    hand_id: int
    button: object
    names: dict             # seat -> bot name
    positions: dict         # seat -> position name
    board: list
    history: list           # HistoryEntry, whole hand (incl. blind posts)
    showdown: dict          # seat -> hole cards, for players who showed
    net: dict               # seat -> chips won (+) or lost (-)
    start_stacks: dict      # seat -> stack at hand start
