from helpers import Notes

from kyo.agents.gateway import GatewayAgent
from kyo.scenarios.store import Product, ScenarioStore


def make(max_steps=5, **store_options):
    products = [Product("A1", "Red lamp", "Lamps", 10.0, {"colour": "red"})]
    store = ScenarioStore(products, **store_options)
    return GatewayAgent(store, max_steps), store, Notes()


def test_lists_the_store_tools():
    gateway, store, context = make()
    assert gateway.on_tools({}, context)["tools"] == store.tools()


def test_call_spends_a_step_and_returns_the_observation():
    gateway, _, context = make()
    reply = gateway.on_call({"name": "get_product", "args": {"product_id": "A1"}}, context)
    assert reply["ok"] and reply["observation"]["title"] == "Red lamp"
    assert reply["left"] == 4 and context.notes == ["step 1: get_product"]


def test_invalid_arguments_spend_no_step():
    gateway, store, context = make()
    reply = gateway.on_call({"name": "get_product", "args": {}}, context)
    assert not reply["ok"] and "args.product_id is required" in reply["error"]
    assert gateway.step == 0 and store.steps == 0


def test_unknown_tool_is_rejected():
    gateway, _, context = make()
    reply = gateway.on_call({"name": "teleport"}, context)
    assert reply == {"ok": False, "error": "unknown tool teleport"}


def test_arguments_are_repaired_before_the_store_sees_them():
    gateway, store, context = make()
    gateway.on_call({"name": "update_cart", "args": {"product_id": "A1", "quantity": "2"}}, context)
    assert store.cart == {"A1": 2}


def test_step_limit_is_enforced():
    gateway, store, context = make(max_steps=1)
    gateway.on_call({"name": "view_cart"}, context)
    assert gateway.on_call({"name": "view_cart"}, context)["error"] == "no steps left"
    assert store.steps == 1


def test_finished_episode_refuses_further_calls():
    gateway, store, context = make()
    assert gateway.on_call({"name": "decline_purchase"}, context)["finished"] is True
    assert gateway.on_call({"name": "view_cart"}, context)["error"] == "the episode has finished"
    assert gateway.on_status({}, context)["finished"] is True


def test_request_can_lower_but_not_raise_the_step_limit():
    gateway, _, context = make(max_steps=5)
    assert gateway.on_limit_steps({"steps": 3}, context)["left"] == 3
    assert gateway.on_limit_steps({"steps": 9}, context)["left"] == 3


def test_observations_can_be_reviewed_since_a_step():
    gateway, _, context = make(notes={2: "Budget is now 5"})
    gateway.on_call({"name": "view_cart"}, context)
    gateway.on_call({"name": "view_cart"}, context)
    entries = gateway.on_observations({"since": 1}, context)["entries"]
    assert [e["step"] for e in entries] == [2]
    assert entries[0]["observation"]["note"] == "Budget is now 5"


def test_a_store_failure_becomes_an_error_observation():
    gateway, store, context = make()

    def broken(args):
        raise ConnectionError("database is locked")

    store._get_product = broken
    reply = gateway.on_call({"name": "get_product", "args": {"product_id": "A1"}}, context)
    assert reply["ok"] and not reply["finished"] and gateway.step == 1
    assert "ConnectionError: database is locked" in reply["observation"]["error"]
