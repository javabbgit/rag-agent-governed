"""The JSON-Schema subset validator and the tool specs."""

import pytest

from rag_agent.agent.errors import SpecInvalid
from rag_agent.agent.schema import check_supported, validate
from rag_agent.agent.specs import load_specs

OBJ = {
    "type": "object",
    "properties": {
        "s": {"type": "string", "minLength": 2, "maxLength": 4, "pattern": "^[a-z]+$"},
        "n": {"type": "number", "minimum": 1, "maximum": 10},
        "i": {"type": "integer", "minimum": 1},
        "e": {"type": "string", "enum": ["a", "b"]},
        "l": {
            "type": "array",
            "items": {"type": "string", "minLength": 2},
            "minItems": 1,
            "maxItems": 2,
        },
        "b": {"type": "boolean"},
    },
    "required": ["s"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"s": "ab"}, None),
        ({}, ("s", "required")),
        ([], ("", "type")),
        ({"s": "ab", "x": 1}, ("", "additionalProperties")),
        ({"s": 5}, ("s", "type")),
        ({"s": "a"}, ("s", "minLength")),
        ({"s": "abcde"}, ("s", "maxLength")),
        ({"s": "AB"}, ("s", "pattern")),
        ({"s": "abc\n"}, ("s", "pattern")),  # fullmatch: no trailing newline
        ({"s": "ab", "n": 0}, ("n", "minimum")),
        ({"s": "ab", "n": 11}, ("n", "maximum")),
        ({"s": "ab", "n": 1.5}, None),
        ({"s": "ab", "n": True}, ("n", "type")),  # bool is never a number
        ({"s": "ab", "i": True}, ("i", "type")),
        ({"s": "ab", "i": 1.0}, None),  # a whole float is an integer
        ({"s": "ab", "i": 1.5}, ("i", "type")),
        ({"s": "ab", "i": 0.0}, ("i", "minimum")),
        ({"s": "ab", "n": float("nan")}, ("n", "type")),  # a number must be finite
        ({"s": "ab", "n": float("inf")}, ("n", "type")),
        ({"s": "ab", "n": float("-inf")}, ("n", "type")),
        ({"s": "ab", "i": float("nan")}, ("i", "type")),
        ({"s": "ab", "i": float("inf")}, ("i", "type")),
        ({"s": "ab", "n": 10**400}, ("n", "maximum")),  # a huge int does not overflow the check
        ({"s": "ab", "e": "c"}, ("e", "enum")),
        ({"s": "ab", "l": []}, ("l", "minItems")),
        ({"s": "ab", "l": ["aa", "bb", "cc"]}, ("l", "maxItems")),
        ({"s": "ab", "l": ["aa", "b"]}, ("l[1]", "minLength")),
        ({"s": "ab", "b": "yes"}, ("b", "type")),
    ],
)
def test_validate(value, expected):
    assert validate(OBJ, value) == expected


def test_non_finite_json_literals_fail_the_number_type():
    import json

    for text in ("NaN", "Infinity", "-Infinity", "1e999"):
        value = json.loads('{"s": "ab", "n": ' + text + "}")
        assert validate(OBJ, value) == ("n", "type"), text


def test_unsupported_keyword_raises_at_load():
    with pytest.raises(SpecInvalid):
        check_supported({"type": "string", "format": "email"})
    with pytest.raises(SpecInvalid):
        check_supported({"type": "object", "properties": {"a": {"oneOf": []}}})
    with pytest.raises(SpecInvalid):
        check_supported({"type": "object", "additionalProperties": True})


def test_repo_specs_are_valid_and_closed():
    specs = load_specs()
    assert len(specs) == 5
    for spec in specs:
        params = spec["function"]["parameters"]
        assert params["additionalProperties"] is False
        check_supported(params)


def test_calculate_payment_has_no_heating_parameter():
    spec = next(s for s in load_specs() if s["function"]["name"] == "calculate_payment")
    assert not any("heating" in k for k in spec["function"]["parameters"]["properties"])


def test_exception_tool_description_says_it_only_prepares_a_draft():
    spec = next(s for s in load_specs() if s["function"]["name"] == "prepare_exception_request")
    text = spec["function"]["description"]
    assert "draft EXC-100" in text and "does not raise or approve" in text
