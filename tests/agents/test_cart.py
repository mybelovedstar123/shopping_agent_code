import pytest
from helpers import Notes

from kyo.agents.cart import CartAgent, _plan_calls
from kyo.agents.gateway import GatewayAgent
from kyo.domain import Item, Listing
from kyo.scenarios.store import Product, ScenarioStore

SETTING = {
    "required": ["id", "n"],
    "properties": {"id": {"type": "string"}, "n": {"type": "integer", "minimum": 0}},
}
ADDING = {
    "required": ["id", "n"],
    "properties": {"id": {"type": "string"}, "n": {"type": "integer", "minimum": -9}},
}
MODAL = {
    "required": ["id", "action"],
    "properties": {
        "id": {"type": "string"},
        "action": {"type": "string", "enum": ["add", "remove"]},
        "n": {"type": "integer"},
    },
}
BARE = {"required": ["id"], "properties": {"id": {"type": "string"}}}


@pytest.mark.parametrize(
    "schema, have, want, drop, calls",
    [
        (SETTING, {}, {"A": 2}, [], [{"id": "A", "n": 2}]),
        (SETTING, {"A": 1}, {"A": 3}, [], [{"id": "A", "n": 3}]),
        (SETTING, {"A": 1}, {}, ["A"], [{"id": "A", "n": 0}]),
        (ADDING, {"A": 1}, {"A": 3}, [], [{"id": "A", "n": 2}]),
        (ADDING, {"A": 2}, {}, ["A"], [{"id": "A", "n": -2}]),
        (MODAL, {}, {"A": 2}, [], [{"id": "A", "action": "add", "n": 2}]),
        (MODAL, {"A": 3}, {"A": 1}, [], [{"id": "A", "action": "remove", "n": 2}]),
        (MODAL, {"A": 3}, {}, ["A"], [{"id": "A", "action": "remove", "n": 3}]),
        (BARE, {}, {"A": 1}, [], [{"id": "A"}]),
        (SETTING, {"A": 2}, {"A": 2}, [], []),
    ],
)
def test_plans_calls_for_each_way_a_cart_tool_can_work(schema, have, want, drop, calls):
    assert _plan_calls(schema, have, want, drop) == calls


def test_cannot_remove_with_a_tool_that_only_adds():
    assert _plan_calls(BARE, {"A": 1}, {}, ["A"]) is None


def team(**store_options):
    products = [
        Product("A", "Lamp", "Lamps", 10.0, stock=2),
        Product("B", "Lamp", "Lamps", 5.0),
    ]
    store = ScenarioStore(products, **store_options)
    gateway = GatewayAgent(store, 10)
    context = Notes({("gateway", "call"): lambda p: gateway.on_call(p, Notes())})
    schema = next(t for t in store.tools() if t["function"]["name"] == "update_cart")
    return CartAgent(), context, schema["function"]["parameters"], store


def test_set_moves_the_store_cart_to_exactly_the_wanted_lines():
    agent, context, schema, store = team()
    agent.on_set({"tool": "update_cart", "schema": schema, "lines": {"A": 1, "B": 2}}, context)
    reply = agent.on_set({"tool": "update_cart", "schema": schema, "lines": {"B": 1}}, context)
    assert reply["ok"] and reply["lines"] == {"B": 1}
    assert store.cart == {"B": 1}


def test_set_spends_no_calls_when_the_cart_is_already_right():
    agent, context, schema, store = team()
    payload = {"tool": "update_cart", "schema": schema, "lines": {"A": 1}}
    agent.on_set(payload, context)
    steps = store.steps
    assert agent.on_set(payload, context)["calls"] == 0
    assert store.steps == steps


def test_store_refusal_is_reported_and_not_recorded_as_a_line():
    agent, context, schema, _ = team()
    reply = agent.on_set({"tool": "update_cart", "schema": schema, "lines": {"A": 9}}, context)
    assert not reply["ok"] and "only 2 of A in stock" in reply["errors"][0]
    assert reply["lines"] == {}


def test_sync_believes_the_store_over_its_own_record():
    agent, context, schema, store = team()
    agent.on_set({"tool": "update_cart", "schema": schema, "lines": {"A": 1}}, context)
    store.cart = {"B": 3}
    reply = agent.on_sync({"tool": "view_cart", "known": ["A", "B"]}, context)
    assert reply["lines"] == {"B": 3}


def faults(lines, records, items, **options):
    agent = CartAgent()
    agent.lines = lines
    payload = {
        "items": [i.to_dict() for i in items],
        "records": [r.to_dict() for r in records],
        **options,
    }
    return agent.on_faults(payload, Notes())["faults"]


LAMP = Listing("A", "Lamp", "Lamps", 10.0)
CHAIR = Listing("C", "Chair", "Chairs", 50.0)
ITEM = Item("lamp", "Lamps")


def test_a_correct_cart_has_no_faults():
    assert faults({"A": 1}, [LAMP], [ITEM], budget=10) == []


def test_missing_item():
    assert faults({}, [LAMP], [ITEM]) == ["item 1 (lamp) has no line in the cart"]


def test_wrong_quantity():
    assert faults({"A": 2}, [LAMP], [ITEM]) == ["A is held at 2, item 1 (lamp) wants 1"]


def test_extra_line_of_another_kind():
    assert faults({"A": 1, "C": 1}, [LAMP, CHAIR], [ITEM]) == [
        "C is not something the shopper asked for"
    ]


def test_two_lines_for_one_item():
    other = Listing("B", "Lamp", "Lamps", 10.0)
    assert faults({"A": 1, "B": 1}, [LAMP, other], [ITEM]) == [
        "item 1 (lamp) has 2 lines: keep one"
    ]


def test_sold_out_line():
    gone = Listing("A", "Lamp", "Lamps", 10.0, in_stock=False)
    assert faults({"A": 1}, [gone], [ITEM]) == ["A is sold out"]


def test_over_budget_in_total_and_per_item():
    item = Item("lamp", "Lamps", 2)
    assert faults({"A": 2}, [LAMP], [item], budget=15) == ["the total is 20, over the budget of 15"]
    assert faults({"A": 2}, [LAMP], [item], budget=15, budget_scope="each") == []
    assert faults({"A": 2}, [LAMP], [item], budget=5, budget_scope="each") == [
        "A costs 10, over the limit of 5 for each item"
    ]
