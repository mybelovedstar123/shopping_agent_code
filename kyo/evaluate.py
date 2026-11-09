"""Score the agent team across shopping scenarios, and write a report.

    python -m kyo.evaluate                  # print the results
    python -m kyo.evaluate --out report.md  # also write a Markdown report

A scenario passes only if the store ends with exactly the expected order, or with none when
the team should decline. "core" scenarios are behaviour the team is built to handle. "stretch"
scenarios are known to be hard, and their failures are reported rather than hidden.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from .models.base import Model
from .models.http import model_from_env
from .scenarios.store import Product, ScenarioStore
from .team import run_team


def _lamp(ident: str, price: float = 20.0, **attributes: Any) -> Product:
    attributes.setdefault("colour", "red")
    return Product(ident, f"Lamp {ident}", "Desk Lamps", price, attributes)


def _mug(ident: str, price: float = 8.0, **attributes: Any) -> Product:
    attributes.setdefault("colour", "white")
    return Product(ident, f"Mug {ident}", "Mugs", price, attributes)


def _chair(ident: str, price: float = 90.0, **attributes: Any) -> Product:
    attributes.setdefault("colour", "black")
    return Product(ident, f"Chair {ident}", "Office Chairs", price, attributes)


QUIET = "Do not ask me anything."


@dataclass
class Scenario:
    name: str
    group: str
    request: str
    products: list[Product]
    expect: dict[str, int] | None
    level: str = "core"
    max_steps: int = 30
    store: dict[str, Any] = field(default_factory=dict)


def scenarios() -> list[Scenario]:
    many_blue = [_lamp(f"B{n}", colour="blue") for n in range(8)]
    return [
        Scenario("one item that fits", "basics", f"Buy a red desk lamp. {QUIET}",
                 [_lamp("A", colour="blue"), _lamp("B")], {"B": 1}),
        Scenario("quantity", "basics", f"Buy 3 white mugs. {QUIET}", [_mug("A")], {"A": 3}),
        Scenario("two items", "basics", f"Buy a red desk lamp and 2 white mugs. {QUIET}",
                 [_lamp("A"), _mug("B")], {"A": 1, "B": 2}),
        Scenario("three items", "basics",
                 f"Buy a red desk lamp, a white mug and a black office chair. {QUIET}",
                 [_lamp("A"), _mug("B"), _chair("C")], {"A": 1, "B": 1, "C": 1}),
        Scenario("fit is on a later page", "basics", f"Buy a red desk lamp. {QUIET}",
                 [*many_blue, _lamp("Z")], {"Z": 1}),
        Scenario("nothing fits, so decline", "declining", f"Buy a purple desk lamp. {QUIET}",
                 [_lamp("A"), _lamp("B", colour="blue")], None),
        Scenario("wrong kind only, so decline", "declining", f"Buy a white office chair. {QUIET}",
                 [_mug("A")], None),
        Scenario("every fit over budget, so decline", "budget",
                 f"Buy a red desk lamp under $10. {QUIET}", [_lamp("A", 20)], None),
        Scenario("budget rules out the first fit", "budget",
                 f"Buy a red desk lamp under $15. {QUIET}", [_lamp("A", 20), _lamp("B", 12)],
                 {"B": 1}),
        Scenario("budget counts quantity", "budget", f"Buy 2 red desk lamps under $30. {QUIET}",
                 [_lamp("A", 20), _lamp("B", 14)], {"B": 2}),
        Scenario("per-item budget", "budget", f"Buy 2 red desk lamps under $25 each. {QUIET}",
                 [_lamp("A", 20)], {"A": 2}),
        Scenario("total budget across items", "budget",
                 f"Buy a red desk lamp and a white mug under $25. {QUIET}",
                 [_lamp("A", 40), _lamp("B", 15), _mug("C", 20), _mug("D", 5)], {"B": 1, "D": 1}),
        Scenario("cheapest across pages", "pick rules", f"Buy the cheapest red desk lamp. {QUIET}",
                 [_lamp("A", 30), _lamp("B", 25), _lamp("C", 40), _lamp("D", 10, colour="blue"),
                  _lamp("E", 15)], {"E": 1}),
        Scenario("largest capacity", "pick rules",
                 f"Buy the mug with the largest capacity. {QUIET}",
                 [_mug("A", capacity="300 ml"), _mug("B", capacity="500 ml"),
                  _mug("C", capacity="400 ml")], {"B": 1}),
        Scenario("lightest", "pick rules", f"Buy the lightest office chair. {QUIET}",
                 [_chair("A", weight="12 kg"), _chair("B", weight="9 kg")], {"B": 1}),
        Scenario("at least a height", "measures", f"Buy a desk lamp at least 40 cm tall. {QUIET}",
                 [_lamp("A", height="30 cm"), _lamp("B", height="45 cm")], {"B": 1}),
        Scenario("units are converted", "measures",
                 f"Buy a desk lamp with a height of at least 12 inch. {QUIET}",
                 [_lamp("A", height="29 cm"), _lamp("B", height="32 cm")], {"B": 1}),
        Scenario("range must fit inside a limit", "measures",
                 f"Buy a desk lamp with a height of at most 40 cm. {QUIET}",
                 [_lamp("A", height="30-50 cm"), _lamp("B", height="30-40 cm")], {"B": 1}),
        Scenario("excluded material", "requirements", f"Buy a desk lamp, not plastic. {QUIET}",
                 [_lamp("A", material="plastic"), _lamp("B", material="brass")], {"B": 1}),
        Scenario("different key decides nothing", "requirements", f"Buy a red desk lamp. {QUIET}",
                 [Product("A", "Lamp A", "Desk Lamps", 9, {"colour": "blue", "cord": "red"}),
                  _lamp("B")], {"B": 1}),
        Scenario("hidden preference found by asking", "interview", "Buy a red desk lamp.",
                 [_lamp("A", finish="matte"), _lamp("B", finish="glossy")], {"B": 1},
                 store={"answers": {"finish": "I'd like glossy."}}),
        Scenario("shopper does not mind", "interview", "Buy a red desk lamp.",
                 [_lamp("A", finish="matte")], {"A": 1},
                 store={"answers": {"finish": "I don't mind."}}),
        Scenario("preference rules everything out", "interview", "Buy a red desk lamp.",
                 [_lamp("A", finish="matte")], None,
                 store={"answers": {"finish": "It has to be glossy."}}),
        Scenario("sold out once carted", "store changes", f"Buy a red desk lamp. {QUIET}",
                 [_lamp("A"), _lamp("B")], {"B": 1}, store={"sell_out_on_cart": ["A"]}),
        Scenario("only fit sells out, so decline", "store changes",
                 f"Buy a red desk lamp. {QUIET}", [_lamp("A")], None,
                 store={"sell_out_on_cart": ["A"]}),
        Scenario("budget changes at checkout", "store changes", f"Buy a red desk lamp. {QUIET}",
                 [_lamp("A", 30), _lamp("B", 10)], {"B": 1},
                 store={"refuse_once": "Your budget is now 15 dollars."}),
        Scenario("requirement changes at checkout", "store changes",
                 f"Buy a red desk lamp. {QUIET}", [_lamp("A"), _lamp("B", colour="blue")],
                 {"B": 1}, store={"refuse_once": "The colour must now be blue."}),
        Scenario("approval given", "process",
                 f"Buy a red desk lamp. {QUIET} Get my approval before ordering.", [_lamp("A")],
                 {"A": 1}, store={"answers": {"May I place": "Yes, go ahead."}}),
        Scenario("approval refused", "process",
                 f"Buy a red desk lamp. {QUIET} Get my approval before ordering.", [_lamp("A")],
                 None, store={"answers": {"May I place": "No, thanks."}}),
        Scenario("few steps but enough", "limits", f"Buy a red desk lamp. {QUIET}",
                 [_lamp("A", colour="blue"), _lamp("B")], {"B": 1}, max_steps=10),
        # Stretch: harder wording and behaviour that needs a model or is not built yet.
        Scenario("number written as a word", "hard wording",
                 f"I'm after a blue mug, nothing over twenty dollars. {QUIET}",
                 [_mug("A", 30, colour="blue"), _mug("B", 15, colour="blue")], {"B": 1}, "stretch"),
        Scenario("describes the use, not the product", "hard wording",
                 f"I need something to drink my coffee from, in red. {QUIET}",
                 [_mug("A", colour="red"), _lamp("B")], {"A": 1}, "stretch"),
        Scenario("colour not in the known list", "hard wording",
                 f"Buy a chartreuse desk lamp. {QUIET}",
                 [_lamp("B", colour="red"), _lamp("A", colour="chartreuse")], {"A": 1}, "stretch"),
        Scenario("unknown word that nothing matches", "hard wording",
                 f"Buy a chartreuse desk lamp. {QUIET}", [_lamp("B", colour="red")], None, "stretch"),
        Scenario("requirement points at another listing", "not built yet",
                 f"Buy a desk lamp in the same colour as the Harbor mug. {QUIET}",
                 [_mug("M", colour="teal"), _lamp("A", colour="red"), _lamp("B", colour="teal")],
                 {"B": 1}, "stretch"),
        Scenario("part must fit the chosen device", "not built yet",
                 f"Buy a drill and a battery that fits it. {QUIET}",
                 [Product("D", "Drill", "Drills", 50, description="Model no. ZX900."),
                  Product("P", "Battery", "Batteries", 9, description="Works with XR 200."),
                  Product("Q", "Battery", "Batteries", 12, description="Works with ZX900.")],
                 {"D": 1, "Q": 1}, "stretch"),
    ]  # fmt: skip


@dataclass
class Outcome:
    scenario: Scenario
    passed: bool
    got: dict[str, int] | None
    result: str
    steps: int
    questions: int
    reason: str
    seconds: float

    @property
    def wrong_purchase(self) -> bool:
        """It bought something it should not have: the worst way to fail."""
        return self.got is not None and self.got != self.scenario.expect


def run_scenario(scenario: Scenario, model: Model | None = None) -> Outcome:
    store = ScenarioStore(list(scenario.products), **scenario.store)
    started = time.perf_counter()
    try:
        result = run_team(scenario.request, store, model=model, max_steps=scenario.max_steps)
        label, reason = result["outcome"], result["reason"]
    except Exception as error:  # a crash is a failed scenario, reported like any other
        label, reason = "crashed", f"{type(error).__name__}: {error}"
    return Outcome(
        scenario,
        store.order == scenario.expect and store.steps <= scenario.max_steps,
        store.order,
        label,
        store.steps,
        len(store.questions),
        reason,
        time.perf_counter() - started,
    )


def evaluate(model: Model | None = None) -> list[Outcome]:
    return [run_scenario(s, model) for s in scenarios()]


def _rate(outcomes: list[Outcome]) -> str:
    passed = sum(o.passed for o in outcomes)
    return f"{passed} of {len(outcomes)}"


def report_markdown(outcomes: list[Outcome], model_name: str = "none (rules only)") -> str:
    core = [o for o in outcomes if o.scenario.level == "core"]
    stretch = [o for o in outcomes if o.scenario.level == "stretch"]
    lines = [
        "# Evaluation",
        "",
        "Generated by `python -m kyo.evaluate --out docs/EVALUATION.md`.",
        "",
        f"- **Model:** {model_name}",
        f"- **Core scenarios passed:** {_rate(core)}",
        f"- **Stretch scenarios passed:** {_rate(stretch)}",
        f"- **Wrong purchases:** {sum(o.wrong_purchase for o in outcomes)}",
        "",
        "A scenario passes only if the store ends with exactly the expected order, or with no",
        "order when the team should decline. A failure that buys the wrong thing is much worse",
        "than one that declines, so wrong purchases are counted separately.",
        "",
        "## What these results do not prove",
        "",
        "- The scenarios were written by the same author as the code, on a small in-memory",
        "  store. They show the workflow behaves as designed, not how well it shops in general.",
        "- With no model, requests are read by a rule parser that handles only simple wording.",
        "- No live model has been evaluated, so these results say nothing about the model paths.",
        "- The team has not been compared with the original `agent.py`, because that agent",
        "  needs a live model and its original test environment.",
        "",
        "## Results",
        "",
        "| Scenario | Group | Level | Result | Steps | Questions |",
        "|---|---|---|---|---|---|",
    ]
    for o in outcomes:
        mark = "pass" if o.passed else ("**WRONG PURCHASE**" if o.wrong_purchase else "declined")
        lines.append(
            f"| {o.scenario.name} | {o.scenario.group} | {o.scenario.level} | {mark} "
            f"| {o.steps} | {o.questions} |"
        )
    failed = [o for o in outcomes if not o.passed]
    if failed:
        lines += ["", "## Failures", ""]
        for o in failed:
            lines.append(
                f"- **{o.scenario.name}:** expected `{o.scenario.expect}`, the store ended with "
                f"`{o.got}`. The team reported {o.result}: {o.reason}"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the agent team across scenarios.")
    parser.add_argument("--out", help="write a Markdown report to this file")
    parser.add_argument("--use-model", action="store_true", help="use the KYO_MODEL_* model")
    args = parser.parse_args(argv)
    model = model_from_env() if args.use_model else None
    if args.use_model and model is None:
        parser.error("--use-model needs KYO_MODEL_BASE_URL and KYO_MODEL_NAME")
    outcomes = evaluate(model)
    for o in outcomes:
        print(f"{'pass' if o.passed else 'FAIL'}  {o.scenario.level:<8} {o.scenario.name}")
    core = [o for o in outcomes if o.scenario.level == "core"]
    wrong = sum(o.wrong_purchase for o in outcomes)
    print(f"\nCore: {_rate(core)}. All: {_rate(outcomes)}. Wrong purchases: {wrong}.")
    if args.out:
        name = model.model if model else "none (rules only)"
        with open(args.out, "w", encoding="utf-8", newline="\n") as report:
            report.write(report_markdown(outcomes, name))
    return 0 if all(o.passed for o in core) and not wrong else 1


if __name__ == "__main__":
    sys.exit(main())
