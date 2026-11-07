import pytest
from helpers import Notes

from kyo.agents.notes import NotesAgent, loose_lines, read_note
from kyo.agents.process import ProcessAgent, read_approval
from kyo.domain import Item
from kyo.models.base import ModelError
from kyo.models.scripted import ScriptedModel

APPROVAL = {"step": "get approval", "when": "order", "messages": 1}
SCHEMA = {"required": ["message"], "properties": {"message": {"type": "string"}}}
CART = {"lines": {"A": 2}, "total": 20.0}


def perform(agent, answers, stage="order", steps=(APPROVAL,), cart=CART):
    asked = []

    def call(payload):
        asked.append(payload)
        if not answers:
            return {"ok": False, "error": "no steps left"}
        return {"ok": True, "observation": {"reply": answers.pop(0)}}

    context = Notes({("gateway", "call"): call})
    payload = {
        "stage": stage,
        "steps": list(steps),
        "cart": cart,
        "ask_tool": "ask",
        "ask_schema": SCHEMA,
        "request": "Buy a lamp",
        "tools": [],
    }
    return agent.on_perform(payload, context), asked


@pytest.mark.parametrize(
    "answer, expected",
    [
        ("Yes, go ahead.", True),
        ("Sounds good", True),
        ("No, too expensive", False),
        ("Please don't", False),
        ("What colour is it?", None),
    ],
)
def test_read_approval(answer, expected):
    assert read_approval(answer) is expected


def test_nothing_due_means_proceed_without_asking():
    reply, asked = perform(ProcessAgent(), [], steps=())
    assert reply["proceed"] and asked == []


def test_steps_for_another_stage_are_not_due():
    reply, asked = perform(ProcessAgent(), [], stage="end")
    assert reply["proceed"] and asked == []


def test_question_names_the_exact_cart_and_total():
    reply, asked = perform(ProcessAgent(), ["Yes"])
    assert reply["proceed"] and reply["done"] == ["get approval"]
    assert asked[0]["args"]["message"] == "May I place this order: 2 x A for a total of 20?"


def test_refusal_stops_the_ending_and_reports_the_reply():
    reply, _ = perform(ProcessAgent(), ["No, too expensive"])
    assert not reply["proceed"] and reply["approved"] is False
    assert reply["reason"] == "No, too expensive"


def test_unclear_reply_is_not_approval():
    reply, _ = perform(ProcessAgent(), ["What colour is it?"])
    assert not reply["proceed"] and reply["approved"] is None


def test_unreachable_shopper_is_not_approval():
    reply, _ = perform(ProcessAgent(), [])
    assert not reply["proceed"] and reply["reason"] == "could not reach the shopper"


def test_approval_holds_only_for_that_cart():
    agent = ProcessAgent()
    perform(agent, ["Yes"])
    again, asked = perform(agent, [])
    assert again["proceed"] and asked == [], "same cart needs no second question"
    changed, asked = perform(agent, ["Yes"], cart={"lines": {"B": 1}, "total": 5.0})
    assert changed["proceed"] and len(asked) == 1, "a different cart must be approved again"


def test_step_without_a_message_is_reported_as_not_done_without_a_model():
    step = {"step": "read the returns page", "when": "order", "messages": 0}
    reply, _ = perform(ProcessAgent(), [], steps=[step])
    assert reply["proceed"] and reply["not_done"] == ["read the returns page"]


def test_model_chooses_the_calls_then_confirms_the_steps_are_done():
    model = ScriptedModel(
        [
            {
                "steps": [{"step": "get approval", "done": False}],
                "call": {"tool": "ask", "args": {"message": "OK?"}},
            },
            {"steps": [{"step": "get approval", "done": True, "lines": [1]}], "call": None},
        ]
    )
    reply, asked = perform(ProcessAgent(model), ["Yes"])
    assert reply["proceed"] and asked[0]["name"] == "ask"


def test_model_hold_stops_the_ending_and_returns_new_requirements():
    needs = [{"item": 1, "need": "colour: blue"}]
    model = ScriptedModel([{"steps": [], "call": None, "hold": "make it blue", "needs": needs}])
    reply, _ = perform(ProcessAgent(model), [])
    assert not reply["proceed"] and reply["needs"] == needs


def test_step_the_model_says_is_missing_blocks_the_ending():
    model = ScriptedModel([{"steps": [{"step": "get approval", "done": False}], "call": None}])
    reply, _ = perform(ProcessAgent(model), [])
    assert not reply["proceed"] and reply["not_done"] == ["get approval"]


def test_rules_take_over_when_the_model_fails():
    reply, asked = perform(ProcessAgent(ScriptedModel([ModelError("down")])), ["Yes"])
    assert reply["proceed"] and asked[0]["args"]["message"].startswith("May I place")


@pytest.mark.parametrize(
    "line, key, value",
    [
        ("Your budget is now 40 dollars.", "budget", 40.0),
        ("The shopper has lowered the budget to $25.50.", "budget", 25.5),
        ("Sorry, L-1 has just sold out.", "gone", ["L-1"]),
        ("That item is out of stock.", "sold_out", True),
        ("The price of this lamp has changed.", "price_moved", True),
        ("The colour must now be blue.", "adds", ["colour: blue"]),
        ("The shopper no longer needs the shade.", "drops", ["shade"]),
    ],
)
def test_read_note(line, key, value):
    assert read_note(line, ["L-1"])[key] == value


def test_sold_out_ids_must_match_whole_ids():
    assert read_note("L-10 has sold out.", ["L-1", "L-10"])["gone"] == ["L-10"]


def test_ordinary_text_is_not_a_change():
    found = read_note("Thanks for shopping with us today.", [])
    assert not any(found.get(k) for k in ("budget", "sold_out", "adds", "drops", "gone"))


def test_loose_lines_skip_listing_fields_and_short_values():
    observation = {
        "title": "A very long product title goes here",
        "note": "Your budget has been changed.",
        "status": "ok",
        "alerts": ["Something else has happened here."],
    }
    assert loose_lines(observation) == [
        "Your budget has been changed.",
        "Something else has happened here.",
    ]


def scan(agent, entries, step=None, known=(), **options):
    log = {"entries": entries, "step": step or len(entries)}
    context = Notes({("gateway", "observations"): lambda p: log})
    items = [Item("lamp", musts=["colour: red"]).to_dict()]
    payload = {"items": items, "known_ids": list(known), **options}
    return agent.on_scan(payload, context), context


def entry(note, name="view", step=1):
    return {"step": step, "name": name, "observation": {"note": note}}


def test_scan_reports_changes_with_their_source_text():
    reply, _ = scan(NotesAgent(), [entry("Your budget is now 40 dollars.")])
    assert reply["budget"] == 40 and reply["notes"] == ["Your budget is now 40 dollars."]


def test_scan_reads_each_note_once():
    agent = NotesAgent()
    scan(agent, [entry("Your budget is now 40 dollars.")])
    reply, _ = scan(agent, [entry("Your budget is now 40 dollars.")])
    assert reply["budget"] is None


def test_scan_asks_only_for_what_is_new():
    agent = NotesAgent()
    _, context = scan(agent, [], step=5)
    calls = []
    context = Notes(
        {("gateway", "observations"): lambda p: calls.append(p) or {"entries": [], "step": 5}}
    )
    agent.on_scan({}, context)
    assert calls == [{"since": 5}]


def test_scan_can_skip_a_tool():
    reply, _ = scan(
        NotesAgent(), [entry("Your budget is now 5 dollars.", "ask")], skip_tools=["ask"]
    )
    assert reply["budget"] is None


def test_model_reads_notes_and_can_ignore_them():
    change = {
        "kind": "change",
        "budget": 30,
        "add": [{"item": 1, "need": "colour: blue"}],
        "gone": ["A", "B"],
    }
    reply, _ = scan(
        NotesAgent(ScriptedModel([change])), [entry("Something has changed today.")], known=["A"]
    )
    assert reply["budget"] == 30 and reply["adds"] == ["colour: blue"] and reply["gone"] == ["A"]
    reply, _ = scan(
        NotesAgent(ScriptedModel([{"kind": "error"}])), [entry("Your budget is now 9.")]
    )
    assert reply["budget"] is None


def test_rules_take_over_when_the_model_fails_on_a_note():
    reply, _ = scan(
        NotesAgent(ScriptedModel([ModelError("down")])), [entry("Your budget is now 9.")]
    )
    assert reply["budget"] == 9
