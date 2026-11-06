import pytest

from kyo.schema import param_named, shape_args, text_params


def shape(value, **schema):
    args, faults = shape_args({"x": value}, {"type": "object", "properties": {"x": schema}})
    return args["x"], faults


@pytest.mark.parametrize(
    "value, kind, expected",
    [
        ("3", "integer", 3),
        (3.0, "integer", 3),
        ("2.5", "number", 2.5),
        (7, "string", "7"),
        ("TRUE", "boolean", True),
        ("x", "array", ["x"]),
    ],
)
def test_values_are_coerced_to_the_schema_type(value, kind, expected):
    assert shape(value, type=kind) == (expected, [])


@pytest.mark.parametrize(
    "value, kind", [("abc", "integer"), (2.5, "integer"), (None, "array"), (True, "integer")]
)
def test_values_that_cannot_be_coerced_are_reported(value, kind):
    _, faults = shape(value, type=kind)
    assert faults == [f"args.x should be {kind}"]


def test_enum_case_is_repaired():
    assert shape("ADD", type="string", enum=["add", "remove"]) == ("add", [])


def test_values_outside_an_enum_are_reported():
    _, faults = shape("swap", type="string", enum=["add"])
    assert faults == ["args.x: allowed values are ['add']"]


def test_long_strings_are_cut_and_short_ones_reported():
    assert shape("abcdef", type="string", maxLength=3) == ("abc", [])
    assert shape("a", type="string", minLength=2)[1] == ["args.x is too short"]


def test_numeric_bounds_are_reported():
    assert shape(0, type="integer", minimum=1)[1] == ["args.x is below 1"]
    assert shape(9, type="integer", maximum=5)[1] == ["args.x is above 5"]


def test_array_sizes_and_items_are_checked():
    assert shape([1, 2, 3], type="array", maxItems=2)[1] == ["args.x allows at most 2 entries"]
    assert shape([], type="array", minItems=1)[1] == ["args.x needs at least 1 entries"]
    assert shape(["1"], type="array", items={"type": "integer"}) == ([1], [])


def test_required_and_unexpected_arguments_are_reported():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    _, faults = shape_args({"b": 1}, schema)
    assert faults == ["args.b is not an allowed argument", "args.a is required"]


def test_unknown_schema_passes_arguments_through():
    assert shape_args({"a": 1}, None) == ({"a": 1}, [])


def test_text_params_skip_enums_and_other_types():
    schema = {
        "properties": {
            "q": {"type": "string"},
            "mode": {"type": "string", "enum": ["a"]},
            "n": {"type": "integer"},
        }
    }
    assert text_params(schema) == ["q"]


def test_param_named_finds_a_parameter_by_pattern():
    schema = {"properties": {"max_price": {}, "page": {}}}
    assert param_named(schema, "page|offset") == "page"
    assert param_named(schema, "query") is None
