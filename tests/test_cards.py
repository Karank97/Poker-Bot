import pytest

from core.cards import (ALL_CLASSES, class_all_combos, card_str, class_combos, evaluate, hand_class,
                        parse_card, parse_cards)


def test_parse_roundtrip():
    for i in range(52):
        assert parse_card(card_str(i)) == i


def test_parse_variants():
    assert parse_card("10h") == parse_card("Th")
    assert parse_card("as") == parse_card("As")
    assert parse_card("A♠") == parse_card("As")
    assert parse_cards("AsKd") == parse_cards("As Kd") == parse_cards("[As, Kd]")
    assert parse_cards(["As", "Kd"]) == parse_cards("As,Kd")
    assert parse_cards("") == []


def test_parse_errors():
    with pytest.raises(ValueError):
        parse_card("Xx")
    with pytest.raises(ValueError):
        parse_cards("As As")


def test_classes():
    assert len(ALL_CLASSES) == 169
    assert sum(class_combos(c) for c in ALL_CLASSES) == 1326
    assert hand_class(parse_card("Kd"), parse_card("As")) == "AKo"
    assert hand_class(parse_card("2s"), parse_card("7s")) == "72s"
    assert hand_class(parse_card("Qh"), parse_card("Qc")) == "QQ"


def test_class_all_combos():
    assert all(len(class_all_combos(c)) == class_combos(c) for c in ALL_CLASSES)
    assert {hand_class(*x) for x in class_all_combos("T9s")} == {"T9s"}


def test_evaluate_order():
    royal = evaluate(parse_cards("As Ks Qs Js Ts 2c 3d"))
    quads = evaluate(parse_cards("Ah Ad Ac As 2c 3d 4h"))
    flush = evaluate(parse_cards("2s 5s 9s Js Ks 3c 4d"))
    pair = evaluate(parse_cards("2s 2d 9s Jc Kh 3c 4d"))
    high = evaluate(parse_cards("2s 7d 9s Jc Kh 3c 4d"))
    assert royal > quads > flush > pair > high
    # 5-card evaluation works too, and the board can play (tie)
    assert evaluate(parse_cards("As Ks Qs Js Ts")) == royal
    assert evaluate(parse_cards("2c 3c As Ks Qs Js Ts")) == evaluate(parse_cards("2d 3d As Ks Qs Js Ts"))
