"""The whole team, shopping end to end on scenario stores with no model."""

import pytest

from kyo.models.base import ModelError
from kyo.models.scripted import ScriptedModel
from kyo.runtime.trace import Trace
from kyo.scenarios.store import Product, ScenarioStore
from kyo.team import run_team


def lamp(ident, price=20.0, stock=10, **attributes):
    attributes.setdefault("colour", "red")
    return Product(ident, f"Lamp {ident}", "Desk Lamps", price, attributes, stock=stock)


def mug(ident, price=8.0, **attributes):
    attributes.setdefault("colour", "white")
    return Product(ident, f"Mug {ident}", "Mugs", price, attributes)


def shop(request, products, max_steps=30, **store_options):
    store = ScenarioStore(products, **store_options)
    result = run_team(request, store, max_steps=max_steps)
    return result, store


def test_buys_the_item_that_fits():
    result, store = shop(
        "Buy a red desk lamp.", [lamp("L1", colour="blue"), lamp("L2", colour="red")]
    )
    assert result["outcome"] == "purchased" and store.order == {"L2": 1}
    assert result["lines"] == {"L2": 1} and result["total"] == 20.0


def test_buys_the_requested_quantity():
    result, store = shop("Buy 2 red desk lamps.", [lamp("L1")])
    assert store.order == {"L1": 2} and result["total"] == 40.0


def test_buys_several_items():
    result, store = shop("Buy a red desk lamp and 2 white mugs.", [lamp("L1"), mug("M1")])
    assert store.order == {"L1": 1, "M1": 2} and result["outcome"] == "purchased"


def test_declines_when_nothing_fits():
    result, store = shop("Buy a green desk lamp.", [lamp("L1"), lamp("L2", colour="blue")])
    assert result["outcome"] == "declined" and store.declined and store.order is None


def test_declines_when_everything_is_over_budget():
    result, store = shop("Buy a red desk lamp under $10.", [lamp("L1")])
    assert result["outcome"] == "declined" and store.order is None


def test_finds_a_fit_on_a_later_page():
    lamps = [lamp(f"L{n}", colour="blue") for n in range(8)] + [lamp("L8", colour="red")]
    result, store = shop("Buy a red desk lamp.", lamps)
    assert store.order == {"L8": 1}


def test_cheapest_pick_reads_all_candidates_and_takes_the_lowest_price():
    lamps = [
        lamp("L1", 30),
        lamp("L2", 25),
        lamp("L3", 40),
        lamp("L4", 10, colour="blue"),
        lamp("L5", 15),
    ]
    result, store = shop("Buy the cheapest red desk lamp.", lamps)
    assert store.order == {"L5": 1}


def test_most_pick_takes_the_largest_measure():
    mugs = [
        mug("M1", capacity="300 ml"),
        mug("M2", capacity="500 ml"),
        mug("M3", capacity="400 ml"),
    ]
    _, store = shop("Buy the mug with the largest capacity.", mugs)
    assert store.order == {"M2": 1}


def test_total_budget_across_items_switches_to_cheaper_listings():
    products = [lamp("L1", 40), lamp("L2", 15), mug("M1", 20), mug("M2", 5)]
    result, store = shop("Buy a red desk lamp and a white mug under $25.", products)
    assert store.order == {"L2": 1, "M2": 1} and result["total"] == 20.0


def test_interview_answers_change_what_is_bought():
    products = [lamp("L1", finish="matte"), lamp("L2", finish="glossy")]
    result, store = shop("Buy a red desk lamp.", products, answers={"finish": "I'd like glossy."})
    assert store.order == {"L2": 1}
    assert "finish: glossy" in result["brief"]["items"][0]["told"]


def test_not_asking_when_told_not_to():
    _, store = shop("Buy a red desk lamp. Do not ask me anything.", [lamp("L1", finish="matte")])
    assert store.questions == []


def test_sold_out_cart_line_is_replaced():
    result, store = shop("Buy a red desk lamp.", [lamp("L1"), lamp("L2")], sell_out_on_cart=["L1"])
    assert result["outcome"] == "purchased" and store.order == {"L2": 1}


def test_declines_when_the_only_fit_sells_out():
    result, store = shop("Buy a red desk lamp.", [lamp("L1")], sell_out_on_cart=["L1"])
    assert result["outcome"] == "declined" and store.order is None


def test_refused_checkout_with_a_budget_note_is_replanned():
    result, store = shop(
        "Buy a red desk lamp.",
        [lamp("L1", 30), lamp("L2", 10)],
        refuse_once="Your budget is now 15 dollars.",
    )
    assert result["outcome"] == "purchased" and store.order == {"L2": 1}


def test_changed_requirement_in_a_note_is_replanned():
    result, store = shop(
        "Buy a red desk lamp. Do not ask me anything.",
        [lamp("L1"), lamp("L2", colour="blue")],
        refuse_once="The colour must now be blue.",
    )
    assert store.order == {"L2": 1}


def test_approval_is_asked_for_the_exact_cart_before_buying():
    _, store = shop(
        "Buy a red desk lamp. Do not ask me anything. Get my approval before ordering.",
        [lamp("L1")],
        answers={"May I place": "Yes, go ahead."},
    )
    assert store.order == {"L1": 1}
    assert store.questions == ["May I place this order: 1 x L1 for a total of 20?"]


def test_refused_approval_means_no_purchase():
    result, store = shop(
        "Buy a red desk lamp. Do not ask me anything. Get my approval before ordering.",
        [lamp("L1")],
        answers={"May I place": "No, that is too expensive."},
    )
    assert result["outcome"] == "declined" and store.order is None and store.cart == {}


def test_step_limit_is_never_exceeded_and_ends_cleanly():
    lamps = [lamp(f"L{n}", colour="blue") for n in range(20)]
    result, store = shop("Buy a red desk lamp.", lamps, max_steps=10)
    assert store.steps <= 10 and result["steps"] == store.steps
    assert result["outcome"] in ("declined", "unfinished")


def test_request_can_set_its_own_step_limit():
    lamps = [lamp(f"L{n}", colour="blue") for n in range(20)]
    _, store = shop("Buy a red desk lamp in 12 steps.", lamps)
    assert store.steps <= 12


def test_no_purchase_ever_breaks_a_stated_requirement():
    """Whatever else happens, the team must not buy what the request rules out."""
    products = [lamp(f"L{n}", 10 + n, colour="blue") for n in range(6)]
    _, store = shop("Buy a red desk lamp under $12.", products)
    assert store.order is None


def test_every_agent_reports_through_the_trace():
    trace = Trace()
    store = ScenarioStore([lamp("L1")])
    run_team("Buy a red desk lamp.", store, trace=trace)
    actors = {event["agent"] for event in trace.events()}
    assert {"coordinator", "request", "catalog", "selection", "cart", "review", "gateway"} <= actors


def test_team_works_when_the_model_is_down():
    store = ScenarioStore([lamp("L1")])
    model = ScriptedModel(lambda prompt: ModelError("down"))
    result = run_team("Buy a red desk lamp.", store, model=model)
    assert result["outcome"] == "purchased" and result["model_review"] == "unavailable"


@pytest.mark.parametrize("calls", [1, 3])
def test_team_survives_running_out_of_model_budget(calls):
    from kyo.models.budget import Budget

    store = ScenarioStore([lamp("L1")])
    model = ScriptedModel(lambda prompt: {"ok": True})
    result = run_team("Buy a red desk lamp.", store, model=model, budget=Budget(calls))
    assert result["outcome"] == "purchased"


def test_an_unknown_word_is_never_ignored():
    lamps = [lamp("A", colour="red"), lamp("B", colour="chartreuse")]
    _, store = shop("Buy a chartreuse desk lamp. Do not ask me anything.", lamps)
    assert store.order == {"B": 1}
    result, store = shop("Buy a chartreuse desk lamp. Do not ask me anything.", [lamp("A")])
    assert result["outcome"] == "declined" and store.order is None


def test_a_spelled_out_budget_is_respected():
    mugs = [mug("A", 30, colour="blue"), mug("B", 15, colour="blue")]
    _, store = shop(
        "I'm after a blue mug, nothing over twenty dollars. Do not ask me anything.", mugs
    )
    assert store.order == {"B": 1}


def test_team_declines_rather_than_buy_on_a_requirement_it_cannot_check():
    products = [lamp("A", colour="red"), mug("M", colour="teal")]
    request = "Buy a desk lamp in the same colour as the Harbor mug. Do not ask me anything."
    result, store = shop(request, products)
    assert result["outcome"] == "declined" and store.order is None
    assert "cannot be checked" in result["reason"]


def test_two_items_one_listing_fits_are_both_bought():
    """Never fewer units than requested, even when one listing suits both items."""
    result, store = shop(
        "Buy a mug and a white mug. Do not ask me anything.", [mug("A"), mug("B", colour="red")]
    )
    assert result["outcome"] == "purchased" and sum(store.order.values()) == 2
    assert store.order.get("A", 0) >= 1, "the white mug must be bought"


def test_two_items_of_one_kind_each_get_their_own_line():
    result, store = shop(
        "Buy a red mug and a blue mug. Do not ask me anything.",
        [mug("R", colour="red"), mug("U", colour="blue")],
    )
    assert result["outcome"] == "purchased" and store.order == {"R": 1, "U": 1}


def test_each_in_another_sentence_does_not_lift_a_total_budget():
    result, store = shop(
        "Buy 2 red desk lamps under $30. Each one must be red. Do not ask me anything.",
        [lamp("L1", 20)],
    )
    assert result["outcome"] == "declined" and store.order is None


class PriceRiseStore(ScenarioStore):
    """Raises a listing's price once it is carted, saying so in the reply when `say` is set."""

    def __init__(self, products, rise, price, say=False, **options):
        super().__init__(products, **options)
        self.rise, self.price, self.say = rise, price, say

    def _update_cart(self, args):
        result = super()._update_cart(args)
        if args["product_id"] == self.rise and args["quantity"] > 0:
            self.products[self.rise].price = self.price
            if self.say:
                result["note"] = f"The price of {self.rise} has increased to {self.price}."
            self.rise = None
        return result


@pytest.mark.parametrize("say", [False, True])
@pytest.mark.parametrize("max_steps", [6, 7, 30])
def test_a_price_rise_after_carting_never_breaks_the_budget(say, max_steps):
    store = PriceRiseStore([lamp("L1", 20)], "L1", 25.0, say=say)
    result = run_team(
        "Buy a red desk lamp under $22. Do not ask me anything.", store, max_steps=max_steps
    )
    assert store.order is None and result["outcome"] != "purchased"


def test_the_reported_total_uses_the_price_read_before_checkout():
    store = PriceRiseStore([lamp("L1", 20)], "L1", 21.0)
    result = run_team("Buy a red desk lamp under $22. Do not ask me anything.", store, max_steps=6)
    assert store.order == {"L1": 1} and result["total"] == 21.0


def test_the_process_steps_cannot_change_the_reviewed_cart():
    brief = {
        "items": [{"name": "desk lamp", "shelf": "Desk Lamps", "musts": ["colour: red"]}],
        "budget": 25,
        "messages": 1,
        "process": [{"step": "get the shopper's approval", "when": "order", "messages": 1}],
        "roles": {
            "search": "search_products",
            "open": "get_product",
            "cart": "update_cart",
            "buy": "checkout",
            "refuse": "decline_purchase",
            "ask": "ask_shopper",
        },
    }
    gate = {
        "steps": [],
        "call": {"tool": "update_cart", "args": {"product_id": "L1", "quantity": 3}},
    }

    def reply(prompt):
        if prompt.startswith("You prepare a shopping task"):
            return brief
        if prompt.startswith("A shopping agent is about to"):
            return gate
        if prompt.startswith("You choose the questions"):
            return {"ask": []}
        return {"ok": True}

    store = ScenarioStore([lamp("L1")], answers={"May I place": "Yes, go ahead."})
    result = run_team(
        "Buy a red desk lamp under $25. Get my approval before ordering.",
        store,
        model=ScriptedModel(reply),
    )
    assert store.order == {"L1": 1} and result["lines"] == {"L1": 1}


def test_a_budget_given_in_an_answer_is_respected():
    store = ScenarioStore(
        [lamp("L1", 20, finish="matte"), lamp("L2", 12, finish="glossy")],
        answers={"": "I don't mind, but I can only spend $15 now."},
    )
    result = run_team("Buy a desk lamp. Ask me at most 1 question.", store)
    assert store.order == {"L2": 1} and result["brief"]["budget"] == 15


GOOD_BRIEF = {
    "items": [
        {"name": "desk lamp", "shelf": "Desk Lamps", "quantity": 1, "musts": ["colour: red"]}
    ],
    "messages": 1,
    "roles": {
        "search": "search_products",
        "open": "get_product",
        "cart": "update_cart",
        "buy": "checkout",
        "refuse": "decline_purchase",
        "ask": "ask_shopper",
    },
}


@pytest.mark.parametrize(
    "replies",
    [
        {"You choose the questions": {"ask": [{"item": "first", "label": "finish"}]}},
        {
            "You choose the questions": {"ask": [{"item": 1, "label": "finish"}]},
            "A shopping agent asked": {"needs": [{"item": "lamp", "need": "finish: matte"}]},
        },
        {"You prepare a shopping task": {**GOOD_BRIEF, "fits": [{"item": 1, "with": 2}]}},
        {"While a shopping agent works": {"kind": "change", "gone": 5, "add": "x"}},
        {"You are the last check": {"ok": False, "problems": 3, "better": "L1"}},
    ],
)
def test_malformed_model_replies_never_end_the_run(replies):
    def reply(prompt):
        for prefix, value in replies.items():
            if prompt.startswith(prefix):
                return value
        if prompt.startswith("You prepare a shopping task"):
            return GOOD_BRIEF
        return {"ask": []} if prompt.startswith("You choose the questions") else "{}"

    store = ScenarioStore([lamp("L1", finish="matte"), lamp("L2", finish="matte")])
    result = run_team("Buy a red desk lamp.", store, model=ScriptedModel(reply))
    assert result["outcome"] in ("purchased", "declined")


class FlakyStore(ScenarioStore):
    """Fails the first product page it is asked for."""

    failed = False

    def _get_product(self, args):
        if not self.failed:
            self.failed = True
            raise ConnectionError("database is locked")
        return super()._get_product(args)


def test_one_store_failure_does_not_end_the_run():
    store = FlakyStore([lamp("L1")])
    result = run_team("Buy a red desk lamp. Do not ask me anything.", store)
    assert result["outcome"] == "purchased" and store.order == {"L1": 1}


def test_a_request_with_a_step_limit_can_still_buy():
    result, store = shop(
        "Buy a red desk lamp within 12 steps. Do not ask me anything.", [lamp("L1")]
    )
    assert result["outcome"] == "purchased" and store.order == {"L1": 1}


def test_a_small_step_limit_in_the_request_is_kept():
    lamps = [lamp(f"L{n}", colour="blue") for n in range(8)] + [lamp("L8")]
    _, store = shop("Buy a red desk lamp in at most 6 steps. Do not ask me anything.", lamps)
    assert store.steps <= 6


def test_a_most_pick_declines_when_no_listing_states_the_measure():
    result, store = shop(
        "Buy the mug with the largest capacity. Do not ask me anything.",
        [mug("A", colour="blue"), mug("B", colour="red")],
    )
    assert result["outcome"] == "declined" and store.order is None
