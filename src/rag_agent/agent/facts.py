"""Policy numbers, read once from corpus/facts.toml. No defaults: a missing key stops startup."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from rag_agent.agent.errors import FactsMissingKey

FACTS_PATH = Path(__file__).resolve().parents[3] / "corpus" / "facts.toml"

REQUIRED_KEYS = (
    "ratios.gds_max_pct",
    "ratios.tds_max_pct",
    "ratios.heating_monthly_cad",
    "ratios.condo_fee_share_pct",
    "qualifying.buffer_pts",
    "qualifying.floor_pct",
    "payment.fixed_compounding",
    "payment.variable_compounding",
    "rates.prime_pct",
    "exceptions.senior_uw_max_pts",
    "exceptions.committee_max_pts",
)

_COMPOUNDING = {"semi-annual": "semi_annual", "monthly": "monthly"}


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True)
class Limits:
    gds_max_pct: Decimal
    tds_max_pct: Decimal


@dataclass(frozen=True)
class Facts:
    limits: Limits
    heating_monthly: Decimal
    condo_share: Decimal  # fraction, e.g. 0.5
    buffer_pts: Decimal
    floor_pct: Decimal
    fixed_compounding: str
    variable_compounding: str
    prime_pct: Decimal
    senior_uw_max_pts: Decimal
    committee_max_pts: Decimal
    rates: dict[str, Any]  # the raw [rates] table, for the per-product keys
    source_path: str = ""

    def key_value(self, key: str) -> Decimal:
        """Numeric value of a dotted facts key that load_facts resolved (authority keys)."""
        table = {
            "exceptions.senior_uw_max_pts": self.senior_uw_max_pts,
            "exceptions.committee_max_pts": self.committee_max_pts,
        }
        if key not in table:
            raise FactsMissingKey(f"facts key not available to the agent: {key}")
        return table[key]

    def qualifying_rate(self, contract_pct: Decimal | float | str) -> tuple[Decimal, str]:
        """max(contract + buffer, floor) per POL-004; returns (rate, rule)."""
        plus_buffer = _dec(contract_pct) + self.buffer_pts
        if plus_buffer >= self.floor_pct:
            return plus_buffer, "contract_plus_buffer"
        return self.floor_pct, "floor"

    def rate_for(self, product_code: str) -> tuple[Decimal, str, str]:
        """(rate_pct, source_key, kind). The key suffix decides fixed vs variable."""
        stem = product_code.replace("-", "")
        fixed = f"{stem}_pct"
        variable = f"{stem}_spread_pts"
        if fixed in self.rates:
            return _dec(self.rates[fixed]), f"rates.{fixed}", "fixed"
        if variable in self.rates:
            rate = self.prime_pct + _dec(self.rates[variable])
            return rate, f"rates.prime_pct + rates.{variable}", "variable"
        raise FactsMissingKey(f"rates.{fixed} or rates.{variable}")

    def compounding_for(self, kind: str) -> str:
        if kind == "fixed":
            return self.fixed_compounding
        if kind == "variable":
            return self.variable_compounding
        raise ValueError(f"unknown product kind: {kind}")


def _get(data: dict[str, Any], key: str, kind: str) -> Any:
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise FactsMissingKey(key)
        node = node[part]
    if kind == "num":
        if isinstance(node, bool) or not isinstance(node, int | float):
            raise FactsMissingKey(f"{key} (not a number)")
    elif not isinstance(node, str) or node not in _COMPOUNDING:
        raise FactsMissingKey(f"{key} (unknown compounding)")
    return node


def parse_facts(data: dict[str, Any], source: str = "") -> Facts:
    def num(key: str) -> Decimal:
        return _dec(_get(data, key, "num"))

    def comp(key: str) -> str:
        return _COMPOUNDING[_get(data, key, "comp")]

    rates = data.get("rates")
    if not isinstance(rates, dict):
        raise FactsMissingKey("rates")
    return Facts(
        limits=Limits(num("ratios.gds_max_pct"), num("ratios.tds_max_pct")),
        heating_monthly=num("ratios.heating_monthly_cad"),
        condo_share=num("ratios.condo_fee_share_pct") / Decimal(100),
        buffer_pts=num("qualifying.buffer_pts"),
        floor_pct=num("qualifying.floor_pct"),
        fixed_compounding=comp("payment.fixed_compounding"),
        variable_compounding=comp("payment.variable_compounding"),
        prime_pct=num("rates.prime_pct"),
        senior_uw_max_pts=num("exceptions.senior_uw_max_pts"),
        committee_max_pts=num("exceptions.committee_max_pts"),
        rates=dict(rates),
        source_path=source,
    )


@lru_cache(maxsize=4)
def _load_cached(path: str) -> Facts:
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise FactsMissingKey(f"facts file is missing or unreadable: {Path(path).name}") from None
    return parse_facts(data, path)


def load_facts(path: str | Path | None = None) -> Facts:
    """Load corpus/facts.toml once per path; any missing key raises FactsMissingKey."""
    return _load_cached(str(path or FACTS_PATH))
