"""A small JSON-Schema subset validator (no dependency). Returns the first error only."""

from __future__ import annotations

import re
from typing import Any

from rag_agent.agent.errors import SpecInvalid

SUPPORTED = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "enum",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "items",
        "minItems",
        "maxItems",
    }
)
TYPES = frozenset({"object", "string", "number", "integer", "array", "boolean"})


def check_supported(schema: Any, where: str = "$") -> None:
    """Raise SpecInvalid for an unsupported keyword anywhere in a schema."""
    if not isinstance(schema, dict):
        raise SpecInvalid(f"schema at {where} is not an object")
    for key, value in schema.items():
        if key not in SUPPORTED:
            raise SpecInvalid(f"unsupported schema keyword {key!r} at {where}")
        if key == "type" and value not in TYPES:
            raise SpecInvalid(f"unsupported type at {where}")
        if key == "additionalProperties" and value is not False:
            raise SpecInvalid(f"additionalProperties must be false at {where}")
        if key == "pattern":
            try:
                re.compile(value)
            except (re.error, TypeError):
                raise SpecInvalid(f"invalid pattern at {where}") from None
        if key == "properties":
            if not isinstance(value, dict):
                raise SpecInvalid(f"properties at {where} is not an object")
            for name, sub in value.items():
                check_supported(sub, f"{where}.{name}")
        if key == "items":
            check_supported(value, f"{where}[]")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _type_ok(kind: str, value: Any) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "string":
        return isinstance(value, str)
    if kind == "number":
        return _is_number(value)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    return isinstance(value, bool)


def _join(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def validate(schema: dict[str, Any], value: Any, path: str = "") -> tuple[str, str] | None:
    """First error as (path, keyword), or None. Path is "" for the root."""
    if "type" in schema and not _type_ok(schema["type"], value):
        return path, "type"
    if "enum" in schema and value not in schema["enum"]:
        return path, "enum"
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            return path, "minLength"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return path, "maxLength"
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            return path, "pattern"
    if _is_number(value):
        if "minimum" in schema and value < schema["minimum"]:
            return path, "minimum"
        if "maximum" in schema and value > schema["maximum"]:
            return path, "maximum"
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                return _join(path, name), "required"
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and any(k not in props for k in value):
            return path, "additionalProperties"  # the unexpected key is never echoed
        for name, sub in props.items():
            if name in value:
                err = validate(sub, value[name], _join(path, name))
                if err:
                    return err
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            return path, "minItems"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return path, "maxItems"
        if "items" in schema:
            for i, item in enumerate(value):
                err = validate(schema["items"], item, f"{path}[{i}]")
                if err:
                    return err
    return None
