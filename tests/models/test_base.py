import pytest

from kyo.models.base import ModelError, ModelReply, ask_json, parse_json_object
from kyo.models.scripted import ScriptedModel


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Sure: {"a": 1}. Done.', {"a": 1}),
        ("[1, 2]", None),
        ("no json here", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_json_object(text, expected):
    assert parse_json_object(text) == expected


def test_ask_json_returns_the_object():
    assert ask_json(ScriptedModel([{"ok": True}]), "check") == {"ok": True}


def test_ask_json_returns_none_for_prose():
    assert ask_json(ScriptedModel(["I am not sure"]), "check") is None


def test_scripted_model_records_prompts_and_runs_out():
    model = ScriptedModel(["one"])
    assert model.complete([{"role": "user", "content": "first"}]).content == "one"
    assert model.prompts == ["first"]
    with pytest.raises(ModelError):
        model.complete([{"role": "user", "content": "second"}])


def test_scripted_model_can_answer_by_rule():
    model = ScriptedModel(lambda prompt: {"echo": prompt})
    assert ask_json(model, "hi") == {"echo": "hi"}


def test_scripted_model_can_raise_and_return_full_replies():
    calls = [{"id": "1"}]
    model = ScriptedModel([ModelError("down"), ModelReply(tool_calls=calls)])
    with pytest.raises(ModelError):
        model.complete([{"content": "x"}])
    assert model.complete([{"content": "x"}]).tool_calls == calls
