import pytest

from kyo.domain import Listing
from kyo.fit import check_need, key_names_property, same_kind, words


def listing(opened=True, **attributes):
    title = attributes.pop("title", "Plain item")
    description = attributes.pop("description", "")
    category = attributes.pop("category", "lamps")
    return Listing("A1", title, category, 10.0, True, attributes, description, opened)


def test_words_normalise_case_plurals_and_camel_case():
    assert words("bodyColours") == ["body", "colour"]
    assert words("Gray Chairs") == ["grey", "chair"]


@pytest.mark.parametrize(
    "key, prop, expected",
    [
        ("colour", "color", True),
        ("body_colour", "colour", True),
        ("bodyColor", "body colour", True),
        ("handle colour", "body colour", False),
        ("weight", "capacity", False),
        ("blade length", "length", True),
    ],
)
def test_key_names_property(key, prop, expected):
    assert key_names_property(key, prop) is expected


@pytest.mark.parametrize(
    "need, value, met",
    [
        # Words: case, plurals, synonyms, and a value containing the wanted words.
        ("colour: teal", "Deep Teal", True),
        ("colour: grey", "gray", True),
        ("colour: sea green", "teal green", False),
        ("finish: roomy", "extra roomy fit", True),
        # One element of a list is enough.
        ("colour: red", "blue, red / green", True),
        ("colour: red", ["blue", "red"], True),
        ("colour: red", "blue, green", False),
        # Closed lists.
        ("colour: red or blue", "blue", True),
        ("colour: red or blue", "green", False),
        # Exclusions.
        ("material: not wicker", "oak", True),
        ("material: not wicker", "wicker", False),
        ("material: other than oak", "oak", False),
        # Limits include the edge.
        ("height: at least 30 cm", "30 cm", True),
        ("height: at least 30 cm", "29 cm", False),
        ("height: at most 30 cm", "30 cm", True),
        ("height: under 30 cm", "31 cm", False),
        ("height: between 10 and 20 cm", "15 cm", True),
        ("height: between 10 and 20 cm", "25 cm", False),
        # Units are converted.
        ("length: at least 12 inch", "31 cm", True),
        ("length: at least 12 inch", "30 cm", False),
        ("weight: at most 1 kg", "900 g", True),
        ("volume: at least 1 l", "500 ml", False),
        # A range must lie wholly inside a limit.
        ("length: at most 20 cm", "10-20 cm", True),
        ("length: at most 20 cm", "10-25 cm", False),
        ("length: at least 10 cm", "5 to 15 cm", False),
        # Exact measures, converted.
        ("capacity: 2 l", "2000 ml", True),
        ("capacity: 2 l", "1500 ml", False),
        # Units that cannot be compared do not count.
        ("size: at least 2 kg", "5 cm", False),
    ],
)
def test_attribute_naming_the_property_decides(need, value, met):
    prop = need.split(":")[0]
    assert check_need(need, listing(**{prop: value})).met is met


def test_a_key_for_a_different_property_decides_nothing():
    item = listing(**{"handle colour": "red", "body colour": "blue"})
    assert check_need("body colour: red", item).met is False


def test_attribute_saying_something_else_overrides_the_title():
    item = listing(title="Red lamp", colour="blue")
    assert check_need("colour: red", item).met is False


def test_value_stated_elsewhere_counts_when_no_attribute_names_the_property():
    assert check_need("style: rustic", listing(title="Rustic lamp")).met is True
    assert check_need("style: rustic", listing(description="A rustic look")).met is True
    assert check_need("style: rustic", listing(features="rustic, modern")).met is True


def test_bare_figure_elsewhere_does_not_meet_a_measurement():
    item = listing(title="Lamp 30 cm", description="Weighs 30 cm")
    assert check_need("capacity: 30 cm", item).met is False


def test_category_label_can_state_the_value():
    assert check_need("season: winter", listing(category="winter coats")).met is True


def test_unread_listing_is_undecided_rather_than_failed():
    assert check_need("colour: red", listing(opened=False)).met is None


def test_unread_listing_is_still_decided_by_what_it_shows():
    assert check_need("colour: red", listing(opened=False, colour="red")).met is True
    assert check_need("colour: red", listing(opened=False, colour="blue")).met is False
    assert check_need("colour: red", listing(opened=False, title="Red lamp")).met is True


def test_read_listing_that_states_nothing_fails():
    assert check_need("colour: red", listing()).met is False


def test_exclusion_without_an_attribute_uses_the_listing_text():
    assert check_need("material: not wicker", listing(title="Wicker chair")).met is False
    assert check_need("material: not wicker", listing(title="Oak chair")).met is True
    assert check_need("material: not wicker", listing(opened=False)).met is None


def test_requirement_without_a_property_is_matched_as_a_value():
    assert check_need("waterproof", listing(title="Waterproof lamp")).met is True


def test_reason_says_why():
    verdict = check_need("colour: red", listing(colour="blue"))
    assert "colour is blue" in verdict.reason and "red" in verdict.reason


@pytest.mark.parametrize(
    "name, shelf, category, expected",
    [
        ("lamp", "Desk Lamps", "desk lamps", True),
        ("lamp", "Desk Lamps", "floor lamps", False),
        ("desk lamp", "", "lamps", True),
        ("lamp", "", "chairs", False),
        ("lamp", "", "", True),
    ],
)
def test_same_kind(name, shelf, category, expected):
    assert same_kind(name, shelf, category) is expected
