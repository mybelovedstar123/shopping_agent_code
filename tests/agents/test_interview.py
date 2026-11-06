import pytest
from helpers import Notes

from kyo.agents.interview import InterviewAgent, read_answer, spoken
from kyo.domain import Item
from kyo.models.base import ModelError
from kyo.models.scripted import ScriptedModel

KEYS = {
    "keys": [
        {"key": "colour", "carried": 5, "values": ["red", "deep blue"]},
        {"key": "height", "carried": 4, "values": ["30 cm"]},
        {"key": "finish", "carried": 1, "values": ["matte"]},
    ],
    "read": 5,
}


def run(answers, items=None, messages=3, model=None, keep=0, left=20):
    """Run an interview. answers is a list of replies; tracks what was asked."""
    questions = []

    def call(payload):
        questions.append(payload["args"]["message"])
        reply = answers.pop(0) if answers else "I don't mind."
        return {"ok": True, "observation": {"reply": reply}, "left": left}

    context = Notes({("catalog", "keys"): lambda p: KEYS, ("gateway", "call"): call})
    agent = InterviewAgent(model)
    schema = {"properties": {"message": {"type": "string"}}, "required": ["message"]}
    result = agent.on_run(
        {
            "items": [i.to_dict() for i in items or [Item("lamp")]],
            "tool": "ask",
            "schema": schema,
            "messages": messages,
            "keep": keep,
            "request": "Buy a lamp",
        },
        context,
    )
    return result, questions


@pytest.mark.parametrize(
    "answer, expected",
    [
        ("I'd like red.", {"need": "colour: red"}),
        ("Red, please", {"need": "colour: red"}),
        ("I'd prefer something deep blue", {"need": "colour: deep blue"}),
        ("Not red", {"need": "colour: not red"}),
        ("I want burgundy.", {"need": "colour: burgundy"}),
        ("I don't mind.", {"free": "colour"}),
        ("No preference", {"free": "colour"}),
        ("", {"free": "colour"}),
        ("Sorry, I don't understand the question.", None),
    ],
)
def test_read_answer(answer, expected):
    assert read_answer(answer, "colour", ["red", "deep blue"]) == expected


def test_spoken_finds_words_in_any_structure():
    assert spoken({"a": {"reply": "red"}, "n": 1}) == "red"
    assert spoken("plain") == "plain"


def test_asks_one_property_per_message_by_coverage():
    _, questions = run(["red", "30 cm", "matte"])
    assert questions == [
        "What colour would you like for the lamp?",
        "What height would you like for the lamp?",
        "What finish would you like for the lamp?",
    ]


def test_answers_become_requirements_and_free_properties():
    result, _ = run(["red", "I don't mind."], messages=2)
    assert result["needs"] == [{"item": 0, "need": "colour: red"}]
    assert result["free"] == [{"item": 0, "property": "height"}]


def test_message_limit_is_respected():
    _, questions = run([], messages=1)
    assert len(questions) == 1
    assert run([], messages=0)[1] == []


def test_settled_and_free_properties_are_not_asked_about():
    item = Item("lamp", musts=["colour: red"], free=["height"])
    _, questions = run([], items=[item])
    assert questions == ["What finish would you like for the lamp?"]


def test_items_with_nothing_found_are_asked_first():
    _, questions = run(["red"], items=[Item("lamp"), Item("mug")], messages=3)
    assert [q.rsplit(" ", 1)[1] for q in questions] == ["lamp?", "mug?", "mug?"]


def test_stops_when_steps_run_low():
    _, questions = run([], keep=20, left=20)
    assert len(questions) == 1


def test_stops_when_the_store_rejects_the_message():
    context = Notes(
        {
            ("catalog", "keys"): lambda p: KEYS,
            ("gateway", "call"): lambda p: {"ok": False, "error": "no steps left"},
        }
    )
    schema = {"required": ["message"]}
    result = InterviewAgent().on_run(
        {"items": [Item("x").to_dict()], "tool": "ask", "schema": schema, "messages": 3}, context
    )
    assert result["asked"] == []


def test_model_plan_goes_first_and_model_reads_answers():
    plan = {"ask": [{"item": 1, "label": "shade fabric", "hint": "looks"}]}
    heard = {"needs": [{"item": 1, "need": "shade fabric: linen", "points": False}], "budget": 40}
    result, questions = run(["linen"], messages=1, model=ScriptedModel([plan, heard]))
    assert questions == ["What shade fabric would you like for the lamp?"]
    assert result["needs"] == [{"item": 0, "need": "shade fabric: linen"}]
    assert result["budget"] == 40


def test_rules_take_over_when_the_model_fails():
    model = ScriptedModel([ModelError("down"), ModelError("down")])
    result, questions = run(["red"], messages=1, model=model)
    assert questions == ["What colour would you like for the lamp?"]
    assert result["needs"] == [{"item": 0, "need": "colour: red"}]
