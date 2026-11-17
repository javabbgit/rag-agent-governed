"""Load and validate eval/agent/tasks.toml (human-owned; read only, never written).

Every loan selector is resolved against the mock data and every gold value is checked against the
code (calc, facts). The first problem raises TaskFileError("<where>", "<what>").
"""

from __future__ import annotations

import hashlib
import string
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from rag_agent.agent import calc, mockdata
from rag_agent.agent.facts import FACTS_PATH, Facts, load_facts
from rag_agent.agent.policy_config import AgentPolicy, load_policy
from rag_agent.agent_eval.checks import is_numeric, to_decimal

TASKS_PATH = Path(__file__).resolve().parents[3] / "eval" / "agent" / "tasks.toml"

TOP_KEYS = {"version", "phrases", "loans", "calc", "facts", "tasks"}
SELECTOR_KEYS = {
    "who",
    "band",
    "in_progress",
    "stage",
    "min_conditions",
    "frequency",
    "expect",
    "gold",
}
BANDS = ("within", "minor", "major", "over_max")
GOLD_NUMERIC = ("amount", "payment", "qualifying_rate", "gds", "tds", "overage")
GOLD_KEYS = {"stage", "conditions", "authority", *GOLD_NUMERIC}
CALC_KEYS = {
    "id",
    "principal",
    "product_code",
    "amortization_years",
    "payment_frequency",
    "payment",
}
FACT_KEYS = {"key", "value"}
TASK_KEYS = {
    "id",
    "split",
    "category",
    "user",
    "question",
    "setup",
    "status",
    "ok",
    "not_ok",
    "order",
    "has",
    "has_any",
    "lacks",
    "cite",
    "drafts",
    "draft_to",
    "request",
    "asks",
    "policy",
    "gold_docs",
}
SPLITS = ("dev", "test")
CATEGORIES = ("single", "multi", "awaiting", "overreach", "policy_refusal", "missing_info")
RUN_STATUSES = (
    "completed",
    "awaiting_raise",
    "policy_stopped",
    "step_limit",
    "blocked",
    "error",
    "rejected_input",
)
REQUEST_KEYS = {"new", "type", "loan", "authority", "overage"}
POLICY_KEYS = {"denied_loan", "requests", "drafts", "lacks"}
SETUP_KINDS = ("own_draft_open",)
SETUP_KEYS = {"kind", "loan"}
EXCEPTABLE = ("senior_underwriter", "credit_committee")
DRAFT_TOOL = "prepare_exception_request"


class TaskFileError(Exception):
    def __init__(self, where: str, what: str):
        super().__init__(f"{where}: {what}")
        self.where, self.what = where, what


@dataclass(frozen=True)
class Selector:
    name: str
    who: str
    expect: str
    band: str | None = None
    in_progress: bool | None = None
    stage: str | None = None
    min_conditions: int | None = None
    frequency: str | None = None
    gold: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicySpec:
    denied_loan: str | None = None
    requests: int | None = None
    drafts: int | None = None
    lacks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Task:
    id: str
    split: str
    category: str
    user: str
    question: str
    setup: dict[str, str] | None = None
    status: tuple[str, ...] = ()
    ok: tuple[str, ...] = ()
    not_ok: tuple[str, ...] = ()
    order: tuple[tuple[str, str], ...] = ()
    has: tuple[str, ...] = ()
    has_any: tuple[tuple[str, ...], ...] = ()
    lacks: tuple[str, ...] = ()
    cite: bool = False
    drafts: int | None = None
    draft_to: str | None = None
    request: dict[str, Any] | None = None
    asks: tuple[tuple[str, ...], ...] = ()
    policy: PolicySpec | None = None
    gold_docs: tuple[str, ...] = ()


@dataclass
class TaskSet:
    path: Path
    sha256: str
    version: int
    phrases: dict[str, list[str]]
    selectors: dict[str, Selector]
    loans: dict[str, str]  # selector name -> loan number
    calc: list[dict[str, Any]]
    facts: list[dict[str, Any]]
    tasks: list[Task]
    gold_numbers: set[Decimal]

    def by_id(self, task_id: str) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)


# --- small helpers -----------------------------------------------------------------------------


def tasks_sha256(path: str | Path | None = None) -> str:
    return hashlib.sha256(Path(path or TASKS_PATH).read_bytes()).hexdigest()


def question_for(task: Task, loans: dict[str, str]) -> str:
    return task.question.format(**loans)


def _num(value: Any, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise TaskFileError(where, f"not a number: {value!r}")
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        raise TaskFileError(where, f"not a number: {value!r}") from None


def _only(data: Any, allowed: set[str], where: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise TaskFileError(where, "must be a table")
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise TaskFileError(where, f"unknown key {unknown[0]!r}")
    return data


def _str_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise TaskFileError(where, "must be a list of non-empty strings")
    return list(value)


def _int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TaskFileError(where, "must be an integer >= 0")
    return value


def _phrase(name: str, phrases: dict[str, list[str]], where: str) -> list[str]:
    if name[1:] not in phrases:
        raise TaskFileError(where, f"unknown phrase list {name!r}")
    return phrases[name[1:]]


def _expand_flat(value: Any, phrases: dict[str, list[str]], where: str) -> tuple[str, ...]:
    out: list[str] = []
    for item in _str_list(value, where):
        out += _phrase(item, phrases, where) if item.startswith("@") else [item]
    return tuple(out)


def _expand_groups(
    value: Any, phrases: dict[str, list[str]], where: str
) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list) or not value:
        raise TaskFileError(where, "must be a non-empty list")
    out: list[tuple[str, ...]] = []
    for item in value:
        if isinstance(item, list):
            out.append(tuple(_str_list(item, where)))
        elif isinstance(item, str) and item.startswith("@"):
            out.append(tuple(_phrase(item, phrases, where)))
        else:
            raise TaskFileError(where, f"item must be a list or an '@name': {item!r}")
    return tuple(out)


# --- selectors and gold ------------------------------------------------------------------------


def larger_overage(loan: dict[str, Any], facts: Facts) -> Decimal:
    r = calc.assess_record(loan, facts).ratios
    return max(r.gds_overage, r.tds_overage, Decimal(0))


def band_of(over: Decimal, facts: Facts) -> str:
    if over <= 0:
        return "within"
    if over <= facts.senior_uw_max_pts:
        return "minor"
    if over <= facts.committee_max_pts:
        return "major"
    return "over_max"


def _passes(sel: Selector, loan: dict[str, Any], facts: Facts) -> bool:
    if sel.band is not None and band_of(larger_overage(loan, facts), facts) != sel.band:
        return False
    in_progress = loan["stage"] in mockdata.IN_PROGRESS_STAGES
    if sel.in_progress is not None and in_progress != sel.in_progress:
        return False
    if sel.stage is not None and loan["stage"] != sel.stage:
        return False
    n_conditions = len(loan["outstanding_conditions"])
    if sel.min_conditions is not None and n_conditions < sel.min_conditions:
        return False
    return sel.frequency is None or loan["payment_frequency"] == sel.frequency


def resolve_selector(
    sel: Selector,
    users: dict[str, mockdata.User],
    loans: list[dict[str, Any]],
    policy: AgentPolicy,
    facts: Facts,
) -> str | None:
    """The first loan by number the user can see that passes every filter."""
    user = users[sel.who]
    scope = policy.roles[user.role].scope
    for loan in sorted(loans, key=lambda r: r["loan_number"]):
        if mockdata.visible_to(loan, user, scope) and _passes(sel, loan, facts):
            return str(loan["loan_number"])
    return None


def loan_payment(loan: dict[str, Any], facts: Facts) -> Decimal:
    """Payment at the loan's own rate, product compounding and payment frequency."""
    _, _, kind = facts.rate_for(loan["product_code"])
    return calc.payment(
        loan["amount"],
        loan["rate_pct"],
        facts.compounding_for(kind),
        loan["payment_frequency"],
        loan["amortization_years"],
    )


def computed_gold(loan: dict[str, Any], facts: Facts) -> dict[str, Any]:
    a = calc.assess_record(loan, facts)
    return {
        "stage": loan["stage"],
        "amount": Decimal(str(loan["amount"])),
        "conditions": set(loan["outstanding_conditions"]),
        "payment": loan_payment(loan, facts),
        "qualifying_rate": calc.pct(a.qualifying_rate_pct),
        "gds": calc.pct(a.ratios.gds),
        "tds": calc.pct(a.ratios.tds),
        "overage": calc.overage_up(larger_overage(loan, facts)),
        "authority": a.authority.required or "none",
    }


def _parse_selector(name: str, raw: Any, users: dict[str, mockdata.User]) -> Selector:
    where = f"loans.{name}"
    data = _only(raw, SELECTOR_KEYS, where)
    for key in ("who", "expect"):
        if not isinstance(data.get(key), str):
            raise TaskFileError(where, f"{key} is required")
    if data["who"] not in users:
        raise TaskFileError(where, f"unknown user {data['who']!r}")
    band = data.get("band")
    if band is not None and band not in BANDS:
        raise TaskFileError(where, f"unknown band {band!r}")
    in_progress = data.get("in_progress")
    if in_progress is not None and not isinstance(in_progress, bool):
        raise TaskFileError(where, "in_progress must be true or false")
    min_cond = data.get("min_conditions")
    if min_cond is not None:
        _int(min_cond, f"{where}.min_conditions")
    gold = _only(data.get("gold", {}), GOLD_KEYS, f"{where}.gold")
    return Selector(
        name,
        data["who"],
        data["expect"],
        band,
        in_progress,
        data.get("stage"),
        min_cond,
        data.get("frequency"),
        dict(gold),
    )


def _check_gold(sel: Selector, loan: dict[str, Any], facts: Facts) -> set[Decimal]:
    """Verify every gold field; return the numeric gold values."""
    computed = computed_gold(loan, facts)
    numbers: set[Decimal] = set()
    for key, expected in sel.gold.items():
        where = f"loans.{sel.name}.gold.{key}"
        got = computed[key]
        if key in GOLD_NUMERIC:
            value = _num(expected, where)
            if value != got:
                raise TaskFileError(where, f"gold {expected} but the code gives {got}")
            numbers.add(value)
        elif key == "conditions":
            if set(_str_list(expected, where)) != got:
                raise TaskFileError(where, f"gold {expected} but the record has {sorted(got)}")
        elif expected != got:
            raise TaskFileError(where, f"gold {expected!r} but the code gives {got!r}")
    return numbers


def _fact_value(data: dict[str, Any], key: str, where: str) -> Any:
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise TaskFileError(where, f"no such key in corpus/facts.toml: {key}")
        node = node[part]
    return node


# --- tasks -------------------------------------------------------------------------------------


def _parse_task(
    raw: Any,
    ctx: dict[str, Any],
) -> Task:
    task_id = raw.get("id") if isinstance(raw, dict) else None
    if not isinstance(task_id, str) or not task_id:
        raise TaskFileError("tasks[?]", "id is required")
    where = f"tasks[{task_id}]"
    data = _only(raw, TASK_KEYS, where)
    users, policy, selectors = ctx["users"], ctx["policy"], ctx["selectors"]
    phrases, loans = ctx["phrases"], ctx["loans_by_number"]
    for key in ("split", "category", "user", "question"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise TaskFileError(where, f"{key} is required")
    if data["split"] not in SPLITS:
        raise TaskFileError(f"{where}.split", f"unknown split {data['split']!r}")
    if data["category"] not in CATEGORIES:
        raise TaskFileError(f"{where}.category", f"unknown category {data['category']!r}")
    user_id = data["user"]
    if user_id not in users:
        raise TaskFileError(f"{where}.user", f"unknown user {user_id!r}")
    role_tools = set(policy.roles[users[user_id].role].tools)
    known_tools = {t for r in policy.roles.values() for t in r.tools}

    for _, name, _, _ in string.Formatter().parse(data["question"]):
        if name is not None and name not in selectors:
            raise TaskFileError(f"{where}.question", f"undefined placeholder {{{name}}}")

    def tools(key: str) -> tuple[str, ...]:
        names = _str_list(data[key], f"{where}.{key}")
        for name in names:
            if name not in known_tools:
                raise TaskFileError(f"{where}.{key}", f"unknown tool {name!r}")
        return tuple(names)

    status = tuple(_str_list(data["status"], f"{where}.status")) if "status" in data else ()
    for s in status:
        if s not in RUN_STATUSES:
            raise TaskFileError(f"{where}.status", f"unknown status {s!r}")
    ok = tools("ok") if "ok" in data else ()
    for name in ok:
        if name not in role_tools:
            raise TaskFileError(f"{where}.ok", f"{name} is not a tool of {user_id}'s role")
    not_ok = tools("not_ok") if "not_ok" in data else ()
    order: list[tuple[str, str]] = []
    for pair in data.get("order", []):
        if not isinstance(pair, list) or len(pair) != 2:
            raise TaskFileError(f"{where}.order", "each item must be [before, after]")
        for name in _str_list(pair, f"{where}.order"):
            if name not in known_tools:
                raise TaskFileError(f"{where}.order", f"unknown tool {name!r}")
        order.append((pair[0], pair[1]))

    cite = data.get("cite", False)
    if "cite" in data and cite is not True:
        raise TaskFileError(f"{where}.cite", "must be true when given")
    drafts = _int(data["drafts"], f"{where}.drafts") if "drafts" in data else None
    draft_to = data.get("draft_to")
    if draft_to is not None and draft_to not in ctx["role_labels"]:
        raise TaskFileError(f"{where}.draft_to", f"unknown contact role_label {draft_to!r}")

    setup = None
    if "setup" in data:
        setup = _only(data["setup"], SETUP_KEYS, f"{where}.setup")
        if setup.get("kind") not in SETUP_KINDS:
            raise TaskFileError(f"{where}.setup", f"unknown kind {setup.get('kind')!r}")
        sel_name = setup.get("loan")
        if sel_name not in selectors:
            raise TaskFileError(f"{where}.setup", f"unknown selector {sel_name!r}")
        loan = loans[ctx["resolved"][sel_name]]
        user = users[user_id]
        if not mockdata.visible_to(loan, user, policy.roles[user.role].scope):
            raise TaskFileError(f"{where}.setup", f"{sel_name} is not visible to {user_id}")
        if DRAFT_TOOL not in role_tools:
            raise TaskFileError(f"{where}.setup", f"{user_id} cannot prepare requests")
        required = calc.assess_record(loan, ctx["facts"]).authority.required
        if required not in EXCEPTABLE:
            raise TaskFileError(f"{where}.setup", f"{sel_name} is not exceptable ({required})")
        setup = dict(setup)

    request = None
    if "request" in data:
        rw = f"{where}.request"
        request = dict(_only(data["request"], REQUEST_KEYS, rw))
        if not isinstance(request.get("new"), bool):
            raise TaskFileError(rw, "new must be true or false")
        if not request["new"]:
            if set(request) != {"new"}:
                raise TaskFileError(rw, "new = false takes no other key")
        else:
            for key in ("type", "loan", "authority"):
                if not isinstance(request.get(key), str):
                    raise TaskFileError(rw, f"{key} is required when new = true")
            types = {"gds_tds_ratio", *policy.minimum_role}
            if request["type"] not in types:
                raise TaskFileError(rw, f"unknown exception type {request['type']!r}")
            if request["loan"] not in selectors:
                raise TaskFileError(rw, f"unknown selector {request['loan']!r}")
            if request["type"] == "gds_tds_ratio":
                loan = loans[ctx["resolved"][request["loan"]]]
                expected = calc.assess_record(loan, ctx["facts"]).authority.required or "none"
            else:
                expected = policy.minimum_role[request["type"]]
            if request["authority"] != expected:
                raise TaskFileError(
                    rw, f"authority {request['authority']!r} but the code gives {expected!r}"
                )
            if "overage" in request:
                _num(request["overage"], f"{rw}.overage")

    pol = None
    if "policy" in data:
        pw = f"{where}.policy"
        p = _only(data["policy"], POLICY_KEYS, pw)
        denied = p.get("denied_loan")
        if denied is not None and denied not in selectors:
            raise TaskFileError(pw, f"unknown selector {denied!r}")
        pol = PolicySpec(
            denied,
            _int(p["requests"], f"{pw}.requests") if "requests" in p else None,
            _int(p["drafts"], f"{pw}.drafts") if "drafts" in p else None,
            _expand_flat(p["lacks"], phrases, f"{pw}.lacks") if "lacks" in p else (),
        )

    return Task(
        id=task_id,
        split=data["split"],
        category=data["category"],
        user=user_id,
        question=data["question"],
        setup=setup,
        status=status,
        ok=ok,
        not_ok=not_ok,
        order=tuple(order),
        has=_expand_flat(data["has"], phrases, f"{where}.has") if "has" in data else (),
        has_any=(
            _expand_groups(data["has_any"], phrases, f"{where}.has_any")
            if "has_any" in data
            else ()
        ),
        lacks=_expand_flat(data["lacks"], phrases, f"{where}.lacks") if "lacks" in data else (),
        cite=bool(cite),
        drafts=drafts,
        draft_to=draft_to,
        request=request,
        asks=_expand_groups(data["asks"], phrases, f"{where}.asks") if "asks" in data else (),
        policy=pol,
        gold_docs=(
            tuple(_str_list(data["gold_docs"], f"{where}.gold_docs")) if "gold_docs" in data else ()
        ),
    )


def _check_numeric_items(tasks: list[Task], gold: set[Decimal]) -> None:
    for task in tasks:
        items = [("has", i) for i in task.has]
        if task.request and task.request.get("overage") is not None:
            items.append(("request.overage", str(task.request["overage"])))
        if task.policy:
            items += [("policy.lacks", i) for i in task.policy.lacks]
        for key, item in items:
            if is_numeric(item) and to_decimal(item) not in gold:
                raise TaskFileError(f"tasks[{task.id}].{key}", f"{item} is not a gold value")


# --- entry point -------------------------------------------------------------------------------


def load_tasks(
    path: str | Path | None = None,
    *,
    facts: Facts | None = None,
    policy: AgentPolicy | None = None,
    users: dict[str, mockdata.User] | None = None,
    loans: list[dict[str, Any]] | None = None,
    contacts: list[dict[str, Any]] | None = None,
    facts_raw: dict[str, Any] | None = None,
) -> TaskSet:
    path = Path(path or TASKS_PATH)
    raw_bytes = path.read_bytes()
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise TaskFileError("file", f"not valid TOML ({exc})") from None
    facts = facts or load_facts()
    policy = policy or load_policy(env={})
    users = users if users is not None else mockdata.load_users()
    loans = loans if loans is not None else mockdata.load_loans()
    contacts = contacts if contacts is not None else mockdata.load_contacts()
    if facts_raw is None:
        facts_raw = tomllib.loads(Path(FACTS_PATH).read_text(encoding="utf-8"))

    _only(data, TOP_KEYS, "file")
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise TaskFileError("version", "must be an integer")

    phrases_raw = _only(data.get("phrases", {}), set(data.get("phrases", {})), "phrases")
    phrases = {name: _str_list(v, f"phrases.{name}") for name, v in phrases_raw.items()}

    # selectors
    selectors: dict[str, Selector] = {}
    resolved: dict[str, str] = {}
    gold_numbers: set[Decimal] = set()
    loans_by_number = {r["loan_number"]: r for r in loans}
    for name, raw in _only(data.get("loans", {}), set(data.get("loans", {})), "loans").items():
        sel = _parse_selector(name, raw, users)
        found = resolve_selector(sel, users, loans, policy, facts)
        if found is None:
            raise TaskFileError(f"loans.{name}", "no loan matches")
        if found != sel.expect:
            raise TaskFileError(f"loans.{name}", f"resolves to {found}, expected {sel.expect}")
        gold_numbers |= _check_gold(sel, loans_by_number[found], facts)
        selectors[name], resolved[name] = sel, found

    # tasks
    if not isinstance(data.get("tasks"), list):
        raise TaskFileError("tasks", "must be an array of tables")
    ctx = {
        "users": users,
        "policy": policy,
        "facts": facts,
        "selectors": selectors,
        "resolved": resolved,
        "phrases": phrases,
        "loans_by_number": loans_by_number,
        "role_labels": {c["role_label"] for c in contacts},
    }
    tasks: list[Task] = []
    seen: set[str] = set()
    for raw in data["tasks"]:
        task = _parse_task(raw, ctx)
        if task.id in seen:
            raise TaskFileError(f"tasks[{task.id}]", "duplicate id")
        seen.add(task.id)
        tasks.append(task)

    # stand-alone calculation gold
    calc_rows: list[dict[str, Any]] = []
    for i, raw in enumerate(data.get("calc", [])):
        row = _only(raw, CALC_KEYS, f"calc[{i}]")
        missing = sorted(CALC_KEYS - set(row))
        if missing:
            raise TaskFileError(f"calc[{i}]", f"{missing[0]} is required")
        where = f"calc[{row['id']}]"
        if row["id"] not in seen:
            raise TaskFileError(where, f"id {row['id']!r} names no task")
        if row["payment_frequency"] not in calc.PAYMENTS_PER_YEAR:
            raise TaskFileError(where, f"unknown frequency {row['payment_frequency']!r}")
        rate, _, kind = facts.rate_for(row["product_code"])
        got = calc.payment(
            _num(row["principal"], f"{where}.principal"),
            rate,
            facts.compounding_for(kind),
            row["payment_frequency"],
            row["amortization_years"],
        )
        expected = _num(row["payment"], f"{where}.payment")
        if expected != got:
            raise TaskFileError(where, f"gold {row['payment']} but the code gives {got}")
        gold_numbers.add(expected)
        calc_rows.append(dict(row))

    # policy numbers
    fact_rows: list[dict[str, Any]] = []
    for i, raw in enumerate(data.get("facts", [])):
        row = _only(raw, FACT_KEYS, f"facts[{i}]")
        if not isinstance(row.get("key"), str) or "value" not in row:
            raise TaskFileError(f"facts[{i}]", "key and value are required")
        where = f"facts[{row['key']}]"
        actual = _num(_fact_value(facts_raw, row["key"], where), where)
        expected = _num(row["value"], where)
        if expected != actual:
            raise TaskFileError(where, f"gold {row['value']} but corpus/facts.toml has {actual}")
        gold_numbers.add(expected)
        fact_rows.append(dict(row))

    _check_numeric_items(tasks, gold_numbers)
    return TaskSet(
        path=path,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        version=version,
        phrases=phrases,
        selectors=selectors,
        loans=resolved,
        calc=calc_rows,
        facts=fact_rows,
        tasks=tasks,
        gold_numbers=gold_numbers,
    )
