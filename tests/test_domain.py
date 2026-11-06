import json

from kyo.domain import Brief, Item, Listing


def test_brief_round_trips_through_json():
    brief = Brief(
        request="Buy a red lamp",
        items=[Item("lamp", "lighting", 2, ["colour: red"])],
        budget=50,
        pick="cheapest",
        roles={"buy": "checkout"},
    )
    assert Brief.from_dict(json.loads(json.dumps(brief.to_dict()))) == brief


def test_listing_round_trips_and_ignores_unknown_keys():
    listing = Listing("A1", "Lamp", "lighting", 9.5, True, {"colour": "red"}, opened=True)
    data = {**listing.to_dict(), "extra": "ignored"}
    assert Listing.from_dict(data) == listing


def test_needs_combine_request_and_shopper_requirements_without_dropped_ones():
    item = Item("lamp", musts=["colour: red", "height: at least 30 cm"], told=["shade: linen"])
    item.dropped.append("colour: red")
    assert item.needs() == ["height: at least 30 cm", "shade: linen"]


def test_add_need_restores_a_dropped_requirement_without_duplicates():
    item = Item("lamp", musts=["colour: red"], dropped=["colour: red"])
    item.add_need("colour: red")
    item.add_need("colour: red")
    assert item.needs() == ["colour: red"]


def test_drop_property_gives_up_all_requirements_on_it():
    item = Item("lamp", musts=["Colour: red", "height: 30 cm"], told=["colour: not blue"])
    assert item.drop_property("colour") == ["Colour: red", "colour: not blue"]
    assert item.needs() == ["height: 30 cm"]
