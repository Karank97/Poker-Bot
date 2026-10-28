"""Card parsing, deck helpers, hand classes and a hand-evaluation wrapper.

Cards are ints 0..51: card = rank_index * 4 + suit_index, with
RANKS = "23456789TJQKA" and SUITS = "cdhs". Evaluation uses eval7 when it is
installed and falls back to treys. `evaluate` always returns "higher is better".
"""
import re

RANKS = "23456789TJQKA"
SUITS = "cdhs"
FULL_DECK = tuple(range(52))

_SUIT_ALIASES = {"♣": "c", "♦": "d", "♥": "h", "♠": "s"}


def make_card(rank: str, suit: str) -> int:
    return RANKS.index(rank) * 4 + SUITS.index(suit)


def rank_of(card: int) -> int:
    return card // 4


def suit_of(card: int) -> int:
    return card % 4


def card_str(card: int) -> str:
    return RANKS[card // 4] + SUITS[card % 4]


def parse_card(text: str) -> int:
    """Parse 'As', 'td', '10h', 'A♠' (case-insensitive rank and suit)."""
    t = text.strip()
    for k, v in _SUIT_ALIASES.items():
        t = t.replace(k, v)
    if t.startswith("10"):
        t = "T" + t[2:]
    if len(t) != 2:
        raise ValueError(f"bad card: {text!r}")
    rank, suit = t[0].upper(), t[1].lower()
    if rank not in RANKS or suit not in SUITS:
        raise ValueError(f"bad card: {text!r}")
    return make_card(rank, suit)


_CARD_RE = re.compile(r"(10|[2-9TJQKAtjqka])([cdhsCDHS♣♦♥♠])")


def parse_cards(text) -> list[int]:
    """Parse 'As Kd', 'As,Kd', 'AsKd', '[As, Kd]' or a list of strings/ints."""
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        return [c if isinstance(c, int) else parse_card(c) for c in text]
    found = _CARD_RE.findall(text)
    cards = [parse_card(r + s) for r, s in found]
    if len(set(cards)) != len(cards):
        raise ValueError(f"duplicate card in {text!r}")
    return cards


def cards_str(cards) -> str:
    return " ".join(card_str(c) for c in cards)


# ---------------------------------------------------------------- hand classes

def hand_class(c1: int, c2: int) -> str:
    """'AKs', 'AKo' or 'QQ'. Higher rank first."""
    r1, r2 = rank_of(c1), rank_of(c2)
    if r1 < r2:
        r1, r2 = r2, r1
    if r1 == r2:
        return RANKS[r1] * 2
    return RANKS[r1] + RANKS[r2] + ("s" if suit_of(c1) == suit_of(c2) else "o")


def all_classes() -> list[str]:
    out = []
    for i in range(12, -1, -1):
        for j in range(12, -1, -1):
            if i == j:
                out.append(RANKS[i] * 2)
            elif i > j:
                out.append(RANKS[i] + RANKS[j] + "s")
                out.append(RANKS[i] + RANKS[j] + "o")
    return out


ALL_CLASSES = all_classes()


def class_combos(cls: str) -> int:
    return 6 if len(cls) == 2 else (4 if cls[2] == "s" else 12)


def class_all_combos(cls: str) -> list[tuple[int, int]]:
    """Every concrete (c1, c2) combo in a hand class."""
    r1, r2 = cls[0], cls[1]
    out = []
    for i, s1 in enumerate(SUITS):
        for j, s2 in enumerate(SUITS):
            if len(cls) == 2:
                ok = i < j
            else:
                ok = (i == j) if cls[2] == "s" else (i != j)
            if ok:
                out.append((make_card(r1, s1), make_card(r2, s2)))
    return out


def class_to_cards(cls: str) -> tuple[int, int]:
    """One representative pair of cards for a hand class."""
    r1, r2 = cls[0], cls[1]
    if len(cls) == 2:
        return make_card(r1, "s"), make_card(r2, "h")
    if cls[2] == "s":
        return make_card(r1, "s"), make_card(r2, "s")
    return make_card(r1, "s"), make_card(r2, "h")


# ----------------------------------------------------------------- evaluation

try:
    import eval7 as _eval7

    BACKEND = "eval7"
    _NATIVE = [_eval7.Card(card_str(i)) for i in range(52)]
    _ev = _eval7.evaluate

    def evaluate(cards) -> int:
        """Strength of the best 5-card hand in 5..7 cards. Higher is better."""
        n = _NATIVE
        return _ev([n[c] for c in cards])

except ImportError:  # pragma: no cover - exercised only without eval7
    from treys import Card as _TCard, Evaluator as _TEval

    BACKEND = "treys"
    _NATIVE = [_TCard.new(card_str(i)) for i in range(52)]
    _tev = _TEval()

    def evaluate(cards) -> int:
        n = _NATIVE
        cs = [n[c] for c in cards]
        return 7463 - _tev.evaluate(cs[:2], cs[2:])


HIGH_CARD, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE, QUADS, STRAIGHT_FLUSH = range(9)


if BACKEND == "eval7":
    def category(score: int) -> int:
        """Hand category 0 (high card) .. 8 (straight flush) of an evaluate() score."""
        return score >> 24
else:  # pragma: no cover
    _TREYS_MAX = (10, 166, 322, 1599, 1609, 2467, 3325, 6185)

    def category(score: int) -> int:
        rank = 7463 - score
        for i, m in enumerate(_TREYS_MAX):
            if rank <= m:
                return 8 - i
        return 0


def board_category(board) -> int:
    """Category of the board alone (3..5 cards)."""
    if len(board) == 5:
        return category(evaluate(board))
    counts = sorted((sum(1 for c in board if rank_of(c) == r) for r in {rank_of(c) for c in board}),
                    reverse=True)
    if counts and counts[0] == 4:
        return QUADS
    if counts and counts[0] == 3:
        return TRIPS
    if len(counts) > 1 and counts[0] == 2 and counts[1] == 2:
        return TWO_PAIR
    return PAIR if counts and counts[0] == 2 else HIGH_CARD


def native_cards():
    """Backend card objects indexed by our int card, for tight loops."""
    return _NATIVE


def native_evaluate():
    """Return a function that evaluates a list of backend card objects."""
    if BACKEND == "eval7":
        return _ev
    return lambda cs: 7463 - _tev.evaluate(cs[:2], cs[2:])
