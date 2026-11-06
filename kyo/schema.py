"""Check and repair tool arguments against a store's JSON schemas before spending a step."""

from __future__ import annotations

import re
from typing import Any


def kinds(schema: dict[str, Any]) -> list[str]:
    kind = schema.get("type")
    if isinstance(kind, str):
        return [kind]
    return [k for k in kind or [] if isinstance(k, str)]


def fits_kind(value: Any, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "null":
        return value is None
    return True


def _coerce(value: Any, wanted: list[str]) -> Any:
    """Convert a value to the first wanted type it can honestly become."""
    for kind in wanted:
        if kind == "integer" and isinstance(value, float) and value.is_integer():
            return int(value)
        if (
            kind in ("integer", "number")
            and isinstance(value, str)
            and re.fullmatch(r"\s*-?\d+(\.\d+)?\s*", value)
        ):
            number = float(value)
            return int(number) if kind == "integer" or number.is_integer() else number
        if kind == "string" and isinstance(value, int | float) and not isinstance(value, bool):
            return str(value)
        if (
            kind == "boolean"
            and isinstance(value, str)
            and value.strip().lower()
            in (
                "true",
                "false",
            )
        ):
            return value.strip().lower() == "true"
        if kind == "array" and not isinstance(value, list) and value is not None:
            return [value]
    return value


def _check(value: Any, schema: Any, where: str, faults: list[str]) -> Any:
    if not isinstance(schema, dict):
        return value
    wanted = kinds(schema)
    if wanted and not any(fits_kind(value, k) for k in wanted):
        value = _coerce(value, wanted)
        if not any(fits_kind(value, k) for k in wanted):
            faults.append(f"{where} should be {'/'.join(wanted)}")
            return value
    options = schema.get("enum")
    if isinstance(options, list) and value not in options:
        if isinstance(value, str):
            same = [o for o in options if isinstance(o, str) and o.lower() == value.strip().lower()]
            value = same[0] if same else value
        if value not in options:
            faults.append(f"{where}: allowed values are {options}")
    if isinstance(value, str):
        if isinstance(schema.get("maxLength"), int):
            value = value[: schema["maxLength"]]
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            faults.append(f"{where} is too short")
    if isinstance(value, int | float) and not isinstance(value, bool):
        if isinstance(schema.get("minimum"), int | float) and value < schema["minimum"]:
            faults.append(f"{where} is below {schema['minimum']}")
        if isinstance(schema.get("maximum"), int | float) and value > schema["maximum"]:
            faults.append(f"{where} is above {schema['maximum']}")
    if isinstance(value, list):
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            faults.append(f"{where} allows at most {schema['maxItems']} entries")
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            faults.append(f"{where} needs at least {schema['minItems']} entries")
        if isinstance(schema.get("items"), dict):
            value = [
                _check(v, schema["items"], f"{where}[{i}]", faults) for i, v in enumerate(value)
            ]
    if isinstance(value, dict):
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        if schema.get("additionalProperties") is False:
            faults += [f"{where}.{n} is not an allowed argument" for n in value if n not in props]
        faults += [
            f"{where}.{n} is required" for n in schema.get("required") or [] if n not in value
        ]
        value = {
            k: _check(v, props[k], f"{where}.{k}", faults) if k in props else v
            for k, v in value.items()
        }
    return value


def shape_args(args: dict[str, Any], schema: Any) -> tuple[dict[str, Any], list[str]]:
    """Return the arguments repaired where possible, and what could not be repaired."""
    faults: list[str] = []
    return _check(args, schema, "args", faults), faults


def text_params(schema: dict[str, Any]) -> list[str]:
    """Free-text string parameters of a tool."""
    props = schema.get("properties") or {}
    return [
        k
        for k, v in props.items()
        if isinstance(v, dict) and "string" in kinds(v) and "enum" not in v
    ]


def param_named(schema: dict[str, Any], pattern: str) -> str | None:
    """The first parameter whose name matches a regular expression."""
    for name in schema.get("properties") or {}:
        if re.search(pattern, name, re.I):
            return name
    return None
