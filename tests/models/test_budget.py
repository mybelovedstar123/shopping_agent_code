import threading

import pytest

from kyo.models.budget import Budget, BudgetedModel, BudgetExceeded
from kyo.models.scripted import ScriptedModel

PROMPT = [{"role": "user", "content": "hi"}]


def test_call_limit_is_enforced():
    budget = Budget(max_calls=1)
    model = BudgetedModel(ScriptedModel(lambda p: "ok"), budget)
    model.complete(PROMPT)
    with pytest.raises(BudgetExceeded, match="call limit of 1"):
        model.complete(PROMPT)


def test_spend_limit_stops_the_next_call():
    budget = Budget(max_calls=10, max_spend=0.05)
    model = BudgetedModel(ScriptedModel(lambda p: "ok", cost=0.03), budget)
    model.complete(PROMPT)
    model.complete(PROMPT)
    assert budget.spent == pytest.approx(0.06)
    with pytest.raises(BudgetExceeded, match="spend limit"):
        model.complete(PROMPT)


def test_child_budgets_also_charge_the_team():
    team = Budget(max_calls=3)
    first, second = team.child(max_calls=2), team.child(max_calls=2)
    a = BudgetedModel(ScriptedModel(lambda p: "ok", cost=0.01), first)
    b = BudgetedModel(ScriptedModel(lambda p: "ok", cost=0.02), second)
    a.complete(PROMPT)
    a.complete(PROMPT)
    b.complete(PROMPT)
    assert team.calls == 3 and team.spent == pytest.approx(0.04)
    with pytest.raises(BudgetExceeded):
        b.complete(PROMPT)
    assert second.calls == 1, "a refused call is not charged to the child"


def test_one_agent_exhausting_its_budget_leaves_others_able_to_work():
    team = Budget(max_calls=10)
    greedy, other = team.child(max_calls=1), team.child(max_calls=1)
    BudgetedModel(ScriptedModel(lambda p: "ok"), greedy).complete(PROMPT)
    with pytest.raises(BudgetExceeded):
        BudgetedModel(ScriptedModel(lambda p: "ok"), greedy).complete(PROMPT)
    BudgetedModel(ScriptedModel(lambda p: "ok"), other).complete(PROMPT)


def test_failed_model_call_still_counts_but_costs_nothing():
    budget = Budget(max_calls=5)
    model = BudgetedModel(ScriptedModel([RuntimeError("down")]), budget)
    with pytest.raises(RuntimeError):
        model.complete(PROMPT)
    assert budget.calls == 1 and budget.spent == 0


def test_limits_hold_across_threads():
    budget = Budget(max_calls=50)
    refused = []

    def work():
        for _ in range(20):
            try:
                budget.reserve_call()
            except BudgetExceeded:
                refused.append(1)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert budget.calls == 50 and len(refused) == 8 * 20 - 50
