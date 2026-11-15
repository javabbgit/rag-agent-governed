"""Citation parsing and validation. Pure functions, no I/O."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

DECLINE = "INSUFFICIENT_CONTEXT"
MIN_WORDS = 4

_MARKER = r"\[\s*S\d+(?:\s*,\s*S\d+)*\s*\]"
MARKER_RE = re.compile(_MARKER)
_LEADING_MARKERS = re.compile(rf"^(?:\s*{_MARKER})+")
_BULLET = re.compile(r"^(?:[-*]|\d+\.)\s+")
_SENTENCE_END = re.compile(
    r"(?<=[.!?])"
    + "".join(
        rf"(?<!\b{a})"
        for a in (r"Co\.", r"Inc\.", r"Ltd\.", r"No\.", r"e\.g\.", r"i\.e\.", r"vs\.")
    )
    + r"\s+"
)
_MARKER_NUMBER = re.compile(r"S(\d+)")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class Validation:
    status: str  # answered | fallback
    subtype: str | None  # sources_only | no_basis | None
    reason: str | None
    units: list[str] = field(default_factory=list)
    cited: list[int] = field(default_factory=list)  # marker numbers, first-appearance order


def _words(unit: str) -> int:
    return len(MARKER_RE.sub("", unit).split())


def split_units(text: str) -> list[str]:
    units: list[str] = []
    for line in text.splitlines():
        line = _BULLET.sub("", line.strip())
        units += [p.strip() for p in _SENTENCE_END.split(line) if p.strip()]

    # markers that start a unit belong to the previous unit
    repaired: list[str] = []
    for unit in units:
        lead = _LEADING_MARKERS.match(unit)
        if lead and repaired:
            repaired[-1] += " " + lead.group(0).strip()
            unit = unit[lead.end() :].strip()
        if unit:
            repaired.append(unit)

    # fragments (for example after "e.g.") join the previous unit, or the next one if first
    merged: list[str] = []
    carry = ""
    for unit in repaired:
        if carry:
            unit, carry = f"{carry} {unit}", ""
        if _words(unit) < MIN_WORDS:
            if merged:
                merged[-1] += " " + unit
            else:
                carry = unit
        else:
            merged.append(unit)
    if carry:
        merged.append(carry)
    return merged


def parse_markers(unit: str) -> list[int]:
    """Source numbers named by the markers in `unit`, in order (duplicates kept)."""
    return [int(n) for m in MARKER_RE.findall(unit) for n in _MARKER_NUMBER.findall(m)]


def validate(text: str, k: int) -> Validation:
    stripped = text.strip()
    if not stripped:
        return Validation("fallback", "sources_only", "empty_output")
    if DECLINE in stripped:
        return Validation("fallback", "no_basis", "model_declined")
    units = split_units(stripped)
    numbers = [parse_markers(u) for u in units]
    if any(n < 1 or n > k for nums in numbers for n in nums):
        return Validation("fallback", "sources_only", "citation_invalid", units)
    if any(not nums or _words(u) == 0 for u, nums in zip(units, numbers, strict=True)):
        return Validation("fallback", "sources_only", "uncited", units)
    cited = list(dict.fromkeys(n for nums in numbers for n in nums))
    return Validation("answered", None, None, units, cited)


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in _NUMBER.findall(text)}


def ungrounded_numbers(
    units: Sequence[str], cited_texts: Mapping[int, str], question: str
) -> list[str]:
    """Units with a number found neither in the chunks they cite nor in the question.

    `cited_texts` maps a source number to that source's text. A warning signal only.
    """
    in_question = _numbers(question)
    flagged = []
    for unit in units:
        allowed = set(in_question)
        for n in parse_markers(unit):
            allowed |= _numbers(cited_texts.get(n, ""))
        if any(num not in allowed for num in _numbers(MARKER_RE.sub("", unit))):
            flagged.append(unit)
    return flagged
