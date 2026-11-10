import pytest
from helpers import Notes

from kyo.agents.review import ReviewAgent
from kyo.compat import fit_problem, marks
from kyo.domain import Brief, Item, Listing
from kyo.models.base import ModelError
from kyo.models.scripted import ScriptedModel


def lamp(ident="A", price=10.0, in_stock=True, opened=True, **attributes):
    return Listing(ident, f"Lamp {ident}", "Lamps", price, in_stock, attributes, "", opened)


ITEM = Item("lamp", "Lamps", 1, ["colour: red"])


def review(lines, records, model=None, assign=None, **brief_options):
    items = brief_options.pop("items", [ITEM])
    brief = Brief(request="Buy a red lamp", items=items, **brief_options)
    payload = {"brief": brief.to_dict(), "lines": lines, "records": [r.to_dict() for r in records]}
    if assign is not None:
        payload["assign"] = assign
    return ReviewAgent(model).on_cart(payload, Notes())


def test_a_right_cart_passes():
    reply = review({"A": 1}, [lamp(colour="red")], budget=10)
    assert reply["ok"] and reply["problems"] == [] and reply["model_review"] == "none"


def test_a_requirement_not_met_is_a_problem_and_rules_the_listing_out():
    reply = review({"A": 1}, [lamp(colour="blue")])
    assert not reply["ok"] and "does not meet colour: red" in reply["problems"][0]
    assert reply["ruled_out"] == ["A"]


def test_an_unread_listing_cannot_pass():
    reply = review({"A": 1}, [lamp(opened=False)])
    assert not reply["ok"], "only a requirement seen to be met counts"


def test_sold_out_line_is_a_problem():
    assert "A is sold out" in review({"A": 1}, [lamp(in_stock=False, colour="red")])["problems"]


def test_wrong_quantity_is_a_problem():
    assert not review({"A": 2}, [lamp(colour="red")])["ok"]


def test_missing_item_is_a_problem():
    reply = review({"A": 1}, [lamp(colour="red")], items=[ITEM, Item("mug", "Mugs")])
    assert "mug has no line in the cart" in reply["problems"]


def test_line_of_the_wrong_kind_is_a_problem():
    chair = Listing("C", "Chair", "Chairs", 5.0, opened=True)
    assert "C is not a kind the shopper asked for" in review({"C": 1}, [chair])["problems"]


def test_over_budget_is_a_problem():
    reply = review({"A": 1}, [lamp(colour="red")], budget=9)
    assert "the total is 10, over the budget of 9" in reply["problems"]


def test_unknown_price_with_a_budget_is_a_problem():
    assert not review({"A": 1}, [lamp(price=None, colour="red")], budget=9)["ok"]


def test_cheaper_fitting_listing_fails_a_cheapest_pick():
    records = [lamp("A", 10, colour="red"), lamp("B", 5, colour="red")]
    reply = review({"A": 1}, records, pick="cheapest")
    assert "B also fits and is cheaper than A" in reply["problems"]
    assert reply["ruled_out"] == ["A"]


def test_cheaper_listing_that_does_not_fit_is_ignored():
    records = [lamp("A", 10, colour="red"), lamp("B", 5, colour="blue")]
    assert review({"A": 1}, records, pick="cheapest")["ok"]


def test_requirements_pointing_at_another_listing_are_left_aside():
    need = "colour: unlike the lamp Ovarno sells"
    item = Item("lamp", "Lamps", musts=[need])
    assert review({"A": 1}, [lamp(colour="blue")], items=[item], lookups=[need])["ok"]


def test_model_gives_a_second_opinion_only_on_a_cart_that_passes_the_rules():
    model = ScriptedModel([{"ok": True}])
    assert review({"A": 1}, [lamp(colour="blue")], model)["model_review"] == "none"
    assert model.prompts == []
    assert review({"A": 1}, [lamp(colour="red")], model)["model_review"] == "passed"


def test_model_objection_fails_the_cart():
    model = ScriptedModel([{"ok": False, "problems": ["looks wrong"], "better": ["B", "nope"]}])
    records = [lamp("A", colour="red"), lamp("B", colour="red")]
    reply = review({"A": 1}, records, model)
    assert not reply["ok"] and reply["problems"] == ["looks wrong"]
    assert reply["better"] == ["B"] and reply["model_review"] == "failed"


@pytest.mark.parametrize("failure", [ModelError("down"), "not json"])
def test_unavailable_model_is_reported_not_counted_as_a_pass(failure):
    reply = review({"A": 1}, [lamp(colour="red")], ScriptedModel([failure]))
    assert reply["model_review"] == "unavailable"
    assert reply["ok"], "the rules passed, and the report shows no model agreed"


def decline(records, model=None, **brief_options):
    items = brief_options.pop("items", [ITEM])
    brief = Brief(request="Buy a red lamp", items=items, **brief_options)
    payload = {"brief": brief.to_dict(), "records": [r.to_dict() for r in records]}
    return ReviewAgent(model).on_decline(payload, Notes())


def test_decline_audit_finds_fits_that_were_missed():
    reply = decline([lamp("A", colour="blue"), lamp("B", colour="red")])
    assert reply["fits"] == {"1": ["B"]} and reply["missing"] == []


def test_decline_audit_lists_rows_worth_reading():
    reply = decline([lamp("A", opened=False)])
    assert reply["unread"] == ["A"] and reply["missing"] == ["lamp"]


def test_decline_audit_ignores_sold_out_and_over_budget_listings():
    records = [lamp("A", in_stock=False, colour="red"), lamp("B", 99, colour="red")]
    assert decline(records, budget=20)["fits"] == {}


def test_decline_audit_can_use_a_model_and_ignores_unknown_ids():
    model = ScriptedModel([{"fits": {"1": ["A", "ghost"]}, "open": [], "reason": "fits"}])
    reply = decline([lamp("A", colour="blue")], model)
    assert reply["fits"] == {"1": ["A"]} and reply["model_review"] == "passed"


def test_decline_audit_reports_an_unavailable_model():
    reply = decline([lamp("A", colour="blue")], ScriptedModel([ModelError("down")]))
    assert reply["model_review"] == "unavailable" and reply["fits"] == {}


def test_marks_read_codes_and_compatibility():
    found = marks("Model no. ZX900. Also sold as AB 123. Works with XR 200, XR 300.")
    assert found["codes"] == ["zx900", "ab 123"]
    assert found["works"] == ["xr 200", "xr 300"]


def test_marks_ignore_negated_compatibility():
    assert marks("Does not fits XR 200.")["works"] == []


def test_fit_problem_compares_part_and_device():
    device = Listing("D", "Device", description="Model no. ZX900.")
    good = Listing("P", "Filter", description="Works with ZX900.")
    bad = Listing("Q", "Filter", description="Works with XR 200.")
    silent = Listing("R", "Filter")
    assert fit_problem(good, device) is None
    assert "works with xr 200" in fit_problem(bad, device)
    assert fit_problem(silent, device) is None, "text that does not say cannot rule it out"


def test_part_that_does_not_fit_the_device_fails_the_cart():
    device = Listing("D", "Drill", "Drills", 50, description="Model no. ZX900.", opened=True)
    part = Listing("P", "Battery", "Batteries", 9, description="Works with XR 200.", opened=True)
    items = [Item("drill", "Drills"), Item("battery", "Batteries")]
    reply = review({"D": 1, "P": 1}, [device, part], items=items, fits=[{"item": 2, "with": 1}])
    assert not reply["ok"] and reply["ruled_out"] == ["P"]


def test_items_of_one_kind_are_each_checked_against_their_own_line():
    items = [Item("lamp", "Lamps", 1, ["colour: red"]), Item("lamp", "Lamps", 1, ["colour: blue"])]
    records = [lamp("A", colour="red"), lamp("B", colour="blue")]
    assert review({"A": 1, "B": 1}, records, items=items)["ok"]
    assigned = review({"A": 1, "B": 1}, records, items=items, assign={"0": "A", "1": "B"})
    assert assigned["ok"]


def test_a_line_shared_by_two_items_must_hold_both_quantities():
    items = [Item("lamp", "Lamps", 1), Item("lamp", "Lamps", 1, ["colour: red"])]
    records = [lamp("A", colour="red")]
    short = review({"A": 1}, records, items=items, assign={"0": "A", "1": "A"})
    assert not short["ok"] and "A is held at 1, lamp and lamp wants 2" in short["problems"]
    assert review({"A": 2}, records, items=items, assign={"0": "A", "1": "A"})["ok"]


def test_a_most_pick_fails_a_line_that_does_not_state_the_measure():
    reply = review({"A": 1}, [lamp(colour="red")], pick="most", pick_measure="height")
    assert "A does not state its height" in reply["problems"] and reply["ruled_out"] == ["A"]
    assert review(
        {"A": 1}, [lamp(colour="red", height="40 cm")], pick="most", pick_measure="height"
    )["ok"]


def test_decline_audit_for_a_most_pick_offers_only_listings_stating_the_measure():
    records = [
        lamp("A", colour="red"),
        lamp("B", colour="red", height="30 cm"),
        lamp("C", colour="red", height="45 cm"),
    ]
    assert decline(records, pick="most", pick_measure="height")["fits"] == {"1": ["C", "B"]}
    assert decline(records[:1], pick="most", pick_measure="height")["fits"] == {}
