"""Pure helpers of the baseline agent that the new code must reproduce."""

import pytest


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Here it is: {"a": 1} done', {"a": 1}),
        ("[1, 2]", None),
        ("not json", None),
        (None, None),
    ],
)
def test_json_obj_finds_one_object(agent, text, expected):
    assert agent._json_obj(text) == expected


@pytest.mark.parametrize(
    "value, expected",
    [(3, 3), (3.0, 3), ("4", 4), ("4.0", 4), (True, None), (3.5, None), ("x", None)],
)
def test_count_accepts_only_whole_numbers(agent, value, expected):
    assert agent._count(value) == expected


def test_shape_coerces_values_to_the_schema(agent):
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}, "mode": {"type": "string", "enum": ["Add"]}},
    }
    faults = []
    shaped = agent._shape({"n": "2", "mode": "add"}, schema, "args", faults)
    assert shaped == {"n": 2, "mode": "Add"}
    assert faults == []


def test_shape_reports_what_it_cannot_fix(agent):
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer", "minimum": 1}},
        "required": ["n"],
        "additionalProperties": False,
    }
    faults = []
    agent._shape({"n": 0, "extra": 1}, schema, "args", faults)
    assert "args.extra is not an allowed argument" in faults
    assert "args.n is below 1" in faults


def test_learn_id_prefers_a_unique_key_named_id(agent):
    rows = [{"id": "A-100", "code": "X1"}, {"id": "A-101", "code": "X1"}]
    assert agent._learn_id(rows) == "id"


def test_fit_marks_reads_compatibility_text(agent):
    marks = agent._fit_marks("Filter [part AB123]. Works with XR 200. Model no. ZX900.")
    assert marks["own"] == "AB123"
    assert "xr 200" in marks["works"]
    assert "zx900" in marks["codes"]
