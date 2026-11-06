import pytest
from helpers import Notes

from kyo.agents.catalog import CatalogAgent, learn_id_key
from kyo.agents.gateway import GatewayAgent
from kyo.models.base import ModelError
from kyo.models.scripted import ScriptedModel
from kyo.scenarios.store import Product, ScenarioStore

LAMPS = [
    Product(f"L-{n}", f"Lamp {n}", "Lamps", 10.0 + n, {"colour": c, "height": f"{30 + n} cm"})
    for n, c in enumerate(["red", "blue", "red", "green", "red", "blue", "red"])
]
MUG = Product("M-1", "Mug", "Mugs", 5.0, {"colour": "red", "sku": "X1"})


def team(products=None, model=None, max_steps=30):
    """A catalog agent wired to a real gateway on a scenario store."""
    store = ScenarioStore(products or [*LAMPS, MUG])
    gateway = GatewayAgent(store, max_steps)
    context = Notes({("gateway", "call"): lambda p: gateway.on_call(p, Notes())})
    tools = {t["function"]["name"]: t["function"]["parameters"] for t in store.tools()}
    return CatalogAgent(model), context, tools, store


def find(agent, context, tools, item, **extra):
    payload = {"tool": "search_products", "schema": tools["search_products"], "item": item, **extra}
    return agent.on_find(payload, context)


def test_learn_id_key_prefers_a_unique_key_named_id():
    rows = [{"title": "A b", "sku": "x1", "id": "1"}, {"title": "C d", "sku": "x2", "id": "2"}]
    assert learn_id_key(rows) == "id"


def test_learn_id_key_finds_a_key_with_another_name():
    assert learn_id_key([{"title": "Red lamp", "code": "AB12"}]) == "code"


def test_find_searches_by_shelf_and_pages_through_results():
    agent, context, tools, store = team()
    reply = find(agent, context, tools, {"name": "lamp", "shelf": "Lamps"})
    assert reply["ids"] == [f"L-{n}" for n in range(7)]
    assert reply["total"] == 7
    assert store.steps == 3


def test_find_uses_words_when_there_is_no_shelf():
    agent, context, tools, _ = team()
    assert find(agent, context, tools, {"name": "mug", "shelf": ""})["ids"] == ["M-1"]


def test_find_can_cap_the_price():
    agent, context, tools, _ = team()
    reply = find(agent, context, tools, {"name": "lamp", "shelf": "Lamps"}, max_price=11.0)
    assert reply["ids"] == ["L-0", "L-1"]


def test_find_respects_the_page_limit_and_steps_to_keep():
    agent, context, tools, store = team()
    find(agent, context, tools, {"name": "lamp", "shelf": "Lamps"}, max_pages=1)
    assert store.steps == 1
    agent, context, tools, store = team(max_steps=4)
    find(agent, context, tools, {"name": "lamp", "shelf": "Lamps"}, keep=3)
    assert store.steps == 1


def test_rows_are_remembered_with_normalised_fields():
    agent, context, tools, _ = team()
    find(agent, context, tools, {"name": "mug", "shelf": ""})
    record = agent.on_records({"ids": ["M-1"]}, context)["records"][0]
    assert record["title"] == "Mug" and record["category"] == "Mugs" and record["price"] == 5.0
    assert record["in_stock"] is True and record["opened"] is False


def test_open_reads_attributes_and_skips_listings_already_open():
    agent, context, tools, store = team()
    find(agent, context, tools, {"name": "mug", "shelf": ""})
    payload = {"tool": "get_product", "schema": tools["get_product"], "ids": ["M-1"]}
    assert agent.on_open(payload, context)["opened"] == ["M-1"]
    steps = store.steps
    assert agent.on_open(payload, context)["opened"] == []
    assert store.steps == steps
    record = agent.on_records({"ids": ["M-1"]}, context)["records"][0]
    assert record["attributes"]["colour"] == "red" and record["opened"] is True


def test_open_stops_at_the_steps_to_keep():
    agent, context, tools, store = team(max_steps=3)
    find(agent, context, tools, {"name": "lamp", "shelf": "Lamps"}, max_pages=1)
    payload = {"tool": "get_product", "schema": tools["get_product"], "ids": ["L-0", "L-1", "L-2"]}
    assert agent.on_open({**payload, "keep": 1}, context)["opened"] == ["L-0"]


def test_records_can_be_filtered_by_item_kind():
    agent, context, tools, _ = team()
    find(agent, context, tools, {"name": "lamp", "shelf": "Lamps"})
    find(agent, context, tools, {"name": "mug", "shelf": ""})
    records = agent.on_records({"item": {"name": "mug", "shelf": ""}}, context)["records"]
    assert [r["id"] for r in records] == ["M-1"]


def test_keys_rank_by_coverage_and_skip_non_properties():
    products = [
        Product("A-1", "Lamp", "Lamps", 1, {"colour": "red", "sku": "1", "features": "dim"}),
        Product("A-2", "Lamp", "Lamps", 1, {"colour": "blue", "height": "3 cm"}),
    ]
    agent, context, tools, _ = team(products)
    item = {"name": "lamp", "shelf": "Lamps"}
    ids = find(agent, context, tools, item)["ids"]
    agent.on_open({"tool": "get_product", "schema": tools["get_product"], "ids": ids}, context)
    reply = agent.on_keys({"item": item}, context)
    assert reply["read"] == 2
    assert [k["key"] for k in reply["keys"]] == ["colour", "height", "feature"]
    assert reply["keys"][0] == {"key": "colour", "carried": 2, "values": ["red", "blue"]}


def test_refresh_reports_changed_listings():
    agent, context, tools, store = team()
    find(agent, context, tools, {"name": "mug", "shelf": ""})
    payload = {"tool": "get_product", "schema": tools["get_product"], "ids": ["M-1"]}
    agent.on_open(payload, context)
    assert agent.on_refresh(payload, context)["changed"] == []
    store.products["M-1"].price = 7.0
    assert agent.on_refresh(payload, context)["changed"] == ["M-1"]
    assert agent.on_records({"ids": ["M-1"]}, context)["records"][0]["price"] == 7.0


def test_sold_listings_stay_sold_even_if_a_row_says_otherwise():
    agent, context, tools, _ = team()
    item = {"name": "mug", "shelf": ""}
    find(agent, context, tools, item)
    agent.on_mark_sold({"ids": ["M-1"]}, context)
    find(agent, context, tools, item, terms="mug red")
    agent.on_find(
        {"tool": "search_products", "schema": tools["search_products"], "item": item}, context
    )
    assert agent.on_records({"ids": ["M-1"]}, context)["records"][0]["in_stock"] is False


class Weird:
    """A store whose rows sit under an unusual key."""

    def __init__(self, reply):
        self.reply = reply

    def request(self, recipient, kind, payload=None):
        return {"ok": True, "observation": self.reply, "left": 9}

    def note(self, text, **data):
        pass


def test_layout_is_found_by_shape_when_key_names_are_unusual():
    observation = {"stuff": [{"id": "Z-1", "name": "Thing", "cost": "$4.50"}]}
    agent = CatalogAgent()
    reply = agent.on_find(
        {"tool": "s", "schema": {"properties": {"q": {"type": "string"}}}, "item": {"name": "x"}},
        Weird(observation),
    )
    assert reply["ids"] == ["Z-1"]
    assert agent.records["Z-1"].price == 4.5 and agent.records["Z-1"].title == "Thing"


@pytest.mark.parametrize("failure", [ModelError("down"), "nonsense"])
def test_model_failure_on_layout_is_not_fatal(failure):
    agent = CatalogAgent(ScriptedModel([failure]))
    reply = agent.on_find(
        {"tool": "s", "schema": {"properties": {"q": {"type": "string"}}}, "item": {"name": "x"}},
        Weird({"nothing": "useful"}),
    )
    assert reply["ids"] == []


def test_model_can_name_the_layout():
    observation = {"payload": {"a": 1}, "weird": 2}
    agent = CatalogAgent(ScriptedModel([{"rows": "payload", "count": "weird", "next": None}]))
    reply = agent.on_find(
        {"tool": "s", "schema": {"properties": {"q": {"type": "string"}}}, "item": {"name": "x"}},
        Weird(observation),
    )
    assert reply["total"] == 2
