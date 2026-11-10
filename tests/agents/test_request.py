import pytest
from helpers import STORE_TOOLS, Notes, tool

from kyo.agents.request import RequestAgent, guess_roles, parse_request
from kyo.domain import Brief
from kyo.models.base import ModelError
from kyo.models.scripted import ScriptedModel


def parse(text):
    return parse_request(text, STORE_TOOLS)


def test_roles_are_guessed_from_tool_names():
    assert guess_roles(STORE_TOOLS) == {
        "search": "search_products",
        "open": "get_product",
        "compare": "compare_products",
        "cart": "update_cart",
        "cart_view": "view_cart",
        "buy": "checkout",
        "refuse": "decline_purchase",
        "ask": "ask_shopper",
    }


def test_roles_fall_back_to_tool_descriptions():
    tools = [tool("t1", "Finds and searches listings"), tool("t2", "Place the order")]
    assert guess_roles(tools) == {"search": "t1", "buy": "t2"}


def test_simple_request():
    brief = parse("I want to buy a red desk lamp.")
    assert len(brief.items) == 1
    item = brief.items[0]
    assert item.name == "desk lamp" and item.quantity == 1
    assert item.shelf == "Desk Lamps"
    assert item.musts == ["colour: red"]


def test_quantities_and_several_items():
    brief = parse("Please get 2 white mugs and a black office chair.")
    assert [(i.name, i.quantity, i.shelf) for i in brief.items] == [
        ("mugs", 2, "Mugs"),
        ("office chair", 1, "Office Chairs"),
    ]
    assert brief.items[0].musts == ["colour: white"]
    assert brief.items[1].musts == ["colour: black"]


@pytest.mark.parametrize(
    "phrase, budget",
    [
        ("under $50", 50),
        ("for less than $19.99", 19.99),
        ("budget of 30 dollars", 30),
        ("at most $5", 5),
        ("my budget is 40", 40),
    ],
)
def test_budget(phrase, budget):
    assert parse(f"Buy a desk lamp {phrase}.").budget == budget


def test_a_measurement_is_not_a_budget():
    brief = parse("Buy a desk lamp at least 30 cm tall.")
    assert brief.budget is None
    assert brief.items[0].musts == ["height: at least 30 cm"]
    assert brief.items[0].name == "desk lamp"


def test_budget_scope_each():
    assert parse("Buy 2 mugs under $5 each.").budget_scope == "each"
    assert parse("Buy 2 mugs under $5.").budget_scope == "total"
    assert parse("Buy 2 mugs, each under $5.").budget_scope == "each"


def test_each_elsewhere_in_the_request_leaves_the_budget_a_total():
    brief = parse("Buy 2 red desk lamps under $30. Each one must be red.")
    assert brief.budget == 30 and brief.budget_scope == "total"


@pytest.mark.parametrize(
    "text, pick, measure",
    [
        ("Buy the cheapest desk lamp.", "cheapest", ""),
        ("Buy a lamp at the lowest price.", "cheapest", ""),
        ("Buy the mug with the largest capacity.", "most", "capacity"),
        ("Buy the lightest chair.", "least", "weight"),
        ("Buy a mug.", "any", ""),
    ],
)
def test_pick_rule(text, pick, measure):
    brief = parse(text)
    assert (brief.pick, brief.pick_measure) == (pick, measure)


def test_cheapest_is_not_part_of_the_item_name():
    assert parse("Buy the cheapest desk lamp.").items[0].name == "desk lamp"


@pytest.mark.parametrize(
    "phrase, need",
    [
        ("with a capacity of 300 ml", "capacity: 300 ml"),
        ("with at least 300 ml capacity", "capacity: at least 300 ml"),
        ("size L", "size: L"),
        ("made of ceramic", "material: ceramic"),
        ("not plastic", "material: not plastic"),
        ("without glass", "material: not glass"),
        ("not black", "colour: not black"),
    ],
)
def test_requirements(phrase, need):
    item = parse(f"Buy a mug {phrase}.").items[0]
    assert item.musts == [need] and item.name == "mug"


def test_story_around_the_request_is_not_a_requirement():
    item = parse("I need a blue mug for my brother because he broke his.").items[0]
    assert item.name == "mug" and item.musts == ["colour: blue"]


def test_message_limits():
    assert parse("Buy a mug.").messages == 3
    assert parse("Buy a mug. Ask me at most 2 questions.").messages == 2
    assert parse("Buy a mug. Do not ask me anything.").messages == 0


def test_no_messages_without_an_ask_tool():
    assert parse_request("Buy a mug.", [tool("checkout")]).messages == 0


def test_approval_becomes_a_process_step():
    brief = parse("Buy a mug, but get my approval before ordering.")
    assert brief.process == [{"step": "get the shopper's approval", "when": "order", "messages": 1}]
    assert brief.items[0].name.startswith("mug")


def test_step_limit():
    assert parse("Buy a mug in 12 steps.").steps == 12


def test_an_unreadable_request_still_gives_one_item():
    assert len(parse("Hello there.").items) == 1


MODEL_BRIEF = {
    "items": [{"name": "kettle", "shelf": "Kettles", "quantity": "2", "musts": ["colour: red"]}],
    "budget": "30",
    "budget_scope": "each",
    "pick": "cheapest",
    "messages": 1,
    "process": [{"step": "get approval", "when": "order", "messages": 1}],
    "roles": {"buy": "checkout", "search": "search_products", "cart": "not_a_tool"},
    "buy_args": {"mode": "buy"},
}


def read(model):
    context = Notes()
    reply = RequestAgent(model).on_read({"request": "Buy kettles", "tools": STORE_TOOLS}, context)
    return Brief.from_dict(reply["brief"]), reply["source"], context


def test_model_brief_is_used_and_cleaned():
    brief, source, _ = read(ScriptedModel([MODEL_BRIEF]))
    assert source == "model"
    assert brief.items[0].quantity == 2 and brief.budget == 30 and brief.pick == "cheapest"
    assert brief.messages == 1 and brief.buy_args == {"mode": "buy"}
    assert brief.roles == {"buy": "checkout", "search": "search_products"}, "unknown tools removed"
    assert brief.request == "Buy kettles"


def test_prompt_includes_the_tools_and_request():
    model = ScriptedModel([MODEL_BRIEF])
    read(model)
    assert "search_products" in model.prompts[0] and "Buy kettles" in model.prompts[0]


@pytest.mark.parametrize(
    "reply",
    [
        ModelError("down"),
        "not json",
        {"items": [], "roles": {"buy": "checkout"}},
        {"items": [{"name": "x"}], "roles": {}},
    ],
)
def test_rules_take_over_when_the_model_fails(reply):
    brief, source, context = read(ScriptedModel([reply]))
    assert source == "rules" and brief.roles["buy"] == "checkout"
    assert context.notes


def test_no_model_uses_rules():
    assert read(None)[1] == "rules"


def test_words_describing_another_listing_are_moved():
    pointer = "colour: unlike the kettle Ovarno sells"
    data = {
        **MODEL_BRIEF,
        "items": [{"name": "kettle", "musts": [pointer, "lid: glass", "size: 2 l"]}],
        "lookups": [pointer],
    }
    brief, _, _ = read(ScriptedModel([data, {"describe_other": ["lid: glass"]}]))
    assert brief.items[0].musts == [f"{pointer} (that listing: lid: glass)", "size: 2 l"]
    assert brief.lookups == [f"{pointer} (that listing: lid: glass)"]


def test_budget_written_in_words():
    assert parse("I'm after a blue mug, nothing over twenty dollars.").budget == 20
    assert parse("Buy a mug for under twenty-five bucks.").budget == 25
    assert parse("Buy a mug under one hundred dollars.").budget == 100


def test_unknown_words_in_the_name_become_a_requirement():
    item = parse("Buy a chartreuse desk lamp.").items[0]
    assert item.shelf == "Desk Lamps" and "chartreuse" in item.musts


def test_a_plain_name_adds_no_requirement():
    assert parse("Buy a desk lamp.").items[0].musts == []
    assert parse("Buy 2 mugs.").items[0].musts == []


def test_requirement_read_from_another_listing_is_recognised():
    brief = parse("Buy a desk lamp in the same colour as the Harbor mug.")
    assert brief.lookups == ["the same colour as the Harbor mug"]
    assert brief.items[0].name == "desk lamp"
