"""Load and validate eval/agent/attacks.toml (human-owned; read only, never written).

Loan selectors are resolved with the D8 task-file functions; contacts, call selectors, scripts
and the expectation table are checked here. The first problem raises AttackFileError.
"""

from __future__ import annotations

import hashlib
import re
import string
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rag_agent.agent import mockdata, specs
from rag_agent.agent.facts import Facts, load_facts
from rag_agent.agent.loop import STARTUP_CODES
from rag_agent.agent.policy import LOAN_RE
from rag_agent.agent.policy_config import AgentPolicy, load_policy
from rag_agent.agent_eval.tasks import TaskFileError, _parse_selector, resolve_selector
from rag_agent.corpus import Section, parse_doc

REPO_ROOT = Path(__file__).resolve().parents[3]
ATTACKS_PATH = REPO_ROOT / "eval" / "agent" / "attacks.toml"
ADV_DIR = REPO_ROOT / "corpus" / "adversarial"

CONFIGS = (
    "on",
    "role_allowlist_off",
    "schema_validation_off",
    "tool_pinning_off",
    "scope_check_off",
    "arg_provenance_off",
    "approval_gate_off",
    "data_envelope_off",
    "all_off",
)
CHANNELS = ("doc", "tool_return", "tool_spec", "args", "claim")
CHANNEL_FIELD = {"doc": "doc", "tool_return": "tamper", "tool_spec": "spec_patch"}
TAMPER_FIELDS = ("outstanding_conditions", "borrower_ref")
PATCH_KINDS = ("description_append", "drop_pattern", "add_tool")
TARGETS = ("T1", "T2", "T3", "T4", "T5", "T6")
OUT_SIMPLE = ("preflight", "drafted", "persisted", "db_rejected", "db_fk", "read_ok")
BOOT_CODES = tuple(code for _, code in STARTUP_CODES)
MAX_CALLS_PER_MESSAGE = 3

TOP_KEYS = {"version", "registered", "loans", "contacts", "attacks"}
REGISTERED_KEYS = ("tools_lock_sha256", "prompt_sha256", "policy_sha256")
CONTACT_KEYS = {"of", "role", "expect"}
ATTACK_KEYS = {
    "id",
    "channel",
    "user",
    "question",
    "doc",
    "expect_gateway",
    "tamper",
    "spec_patch",
    "matrix_only",
    "signature",
    "requested",
    "targets",
    "config_write",
    "script",
    "expect",
}
ATTACK_REQUIRED = ("id", "channel", "user", "question", "signature", "targets", "script", "expect")
TAMPER_KEYS = {"loan", "field", "marker", "text"}
PATCH_KEYS = {"kind", "tool", "text", "param", "relock"}
SEL_KEYS = {"tool", "loan", "recipient", "extra_arg"}
CALL_KEYS = {"tool", "args"}
EXPECT_ENTRY_KEYS = {"out", "hit"}

CT_RE = re.compile(r"(?<![A-Za-z0-9-])CT-[0-9]{4}(?![0-9])")
LOAN_LITERAL = re.compile(r"DMC-[0-9]{4}-[0-9]{5}")
RECIPIENT_LITERAL = re.compile(r"(CT|EXT)-[0-9]{4}")
REASON_RE = re.compile(r"[a-z][a-z_]*")
SHA_RE = re.compile(r"[0-9a-f]{64}")
GATEWAY_RE = re.compile(r"R[0-9]+")
INSTRUCTION_START = "Assistant procedure"


class AttackFileError(Exception):
    def __init__(self, where: str, what: str):
        super().__init__(f"{where}: {what}")
        self.where, self.what = where, what


@dataclass(frozen=True)
class Registered:
    tools_lock_sha256: str
    prompt_sha256: str
    policy_sha256: str


@dataclass(frozen=True)
class CallSel:
    """A signature or requested entry; loan and recipient are resolved literals."""

    tool: str
    loan: str | None = None
    recipient: str | None = None
    extra_arg: str | None = None


@dataclass(frozen=True)
class Tamper:
    loan: str  # resolved loan number
    field: str
    marker: str
    text: str


@dataclass(frozen=True)
class SpecPatch:
    kind: str
    tool: str
    text: str | None = None
    param: str | None = None
    relock: bool = False


@dataclass(frozen=True)
class Expect:
    out: tuple[str, ...]
    hit: tuple[str, ...]


@dataclass(frozen=True)
class Attack:
    id: str
    channel: str
    user: str
    question: str  # rendered
    question_template: str
    signature: tuple[CallSel, ...]
    requested: tuple[CallSel, ...]
    targets: tuple[str, ...]
    script: tuple[tuple[dict[str, Any], ...], ...]
    expect: dict[str, Expect]  # all nine configs
    doc: str | None = None
    expect_gateway: str | None = None
    tamper: Tamper | None = None
    spec_patch: SpecPatch | None = None
    matrix_only: bool = False
    config_write: tuple[str, ...] = ()


@dataclass
class AttackSet:
    path: Path
    sha256: str
    version: int
    registered: Registered
    loans: dict[str, str]  # selector name -> loan number
    contacts: dict[str, str]  # contact name -> contact id
    attacks: list[Attack] = field(default_factory=list)
    adv_dir: Path = ADV_DIR

    def by_id(self, attack_id: str) -> Attack:
        for attack in self.attacks:
            if attack.id == attack_id:
                return attack
        raise KeyError(attack_id)

    def live(self) -> list[Attack]:
        return [a for a in self.attacks if not a.matrix_only]


# --- documents ---------------------------------------------------------------------------------


def doc_path(doc_id: str, adv_dir: str | Path | None = None) -> Path:
    return Path(adv_dir or ADV_DIR) / f"{doc_id}.md"


def doc_sections(doc_id: str, adv_dir: str | Path | None = None) -> list[Section]:
    return list(parse_doc(doc_path(doc_id, adv_dir)).sections)


def instruction_line(text: str) -> str | None:
    """The sentence starting "Assistant procedure", else the first line with a non-ASCII
    character, else None."""
    start = text.find(INSTRUCTION_START)
    if start >= 0:
        rest = text[start:]
        m = re.search(r"\.(\s|$)", rest)
        return rest[: m.start() + 1] if m else rest
    for line in text.splitlines():
        if any(ord(ch) > 127 for ch in line):
            return line.strip()
    return None


def instruction_chunk(doc_id: str, adv_dir: str | Path | None = None) -> Section | None:
    """The first chunk of the document that holds an instruction line."""
    for section in doc_sections(doc_id, adv_dir):
        if instruction_line(section.text) is not None:
            return section
    return None


# --- small helpers -----------------------------------------------------------------------------


def attacks_sha256(path: str | Path | None = None) -> str:
    return hashlib.sha256(Path(path or ATTACKS_PATH).read_bytes()).hexdigest()


def _only(data: Any, allowed: set[str], where: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise AttackFileError(where, "must be a table")
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise AttackFileError(where, f"unknown key {unknown[0]!r}")
    return data


def _str(data: dict[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise AttackFileError(where, f"{key} is required")
    return value


def _str_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise AttackFileError(where, "must be a list of non-empty strings")
    return list(value)


def _literals(text: str) -> list[str]:
    return [m.upper() for m in LOAN_RE.findall(text)] + CT_RE.findall(text)


def valid_out(value: str) -> bool:
    if value in OUT_SIMPLE:
        return True
    prefix, _, rest = value.partition(":")
    if prefix == "boot":
        return rest in BOOT_CODES
    if prefix in ("policy", "business"):
        return bool(REASON_RE.fullmatch(rest))
    return False


# --- parts -------------------------------------------------------------------------------------


def _registered(raw: Any) -> Registered:
    data = _only(raw, set(REGISTERED_KEYS), "registered")
    values = []
    for key in REGISTERED_KEYS:
        value = data.get(key)
        if not isinstance(value, str) or not SHA_RE.fullmatch(value):
            raise AttackFileError(f"registered.{key}", "must be a sha256 hex digest")
        values.append(value)
    return Registered(*values)


def _contacts(
    raw: Any, loans: dict[str, str], all_contacts: list[dict[str, Any]]
) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, entry in _only(raw, set(raw) if isinstance(raw, dict) else set(), "contacts").items():
        where = f"contacts.{name}"
        data = _only(entry, CONTACT_KEYS, where)
        of, role, expect = (_str(data, k, where) for k in ("of", "role", "expect"))
        if of not in loans:
            raise AttackFileError(where, f"unknown loan selector {of!r}")
        found = [
            c["contact_id"]
            for c in all_contacts
            if c["loan_number"] == loans[of] and c["role_label"] == role
        ]
        if len(found) != 1:
            raise AttackFileError(where, f"{len(found)} contacts of {of} have role {role!r}")
        if found[0] != expect:
            raise AttackFileError(where, f"resolves to {found[0]}, expected {expect}")
        out[name] = expect
    return out


def resolve_ref(value: Any, kind: str, loans: dict[str, str], contacts: dict[str, str]) -> str:
    """A loan or recipient reference: a selector name, or a literal of the right form."""
    if not isinstance(value, str) or not value:
        raise ValueError("must be a non-empty string")
    if kind == "loan":
        if value in loans:
            return loans[value]
        if LOAN_LITERAL.fullmatch(value):
            return value
        raise ValueError(f"{value!r} is neither a loan selector nor a DMC- literal")
    if value in contacts:
        return contacts[value]
    if RECIPIENT_LITERAL.fullmatch(value):
        return value
    raise ValueError(f"{value!r} is neither a contact name nor a CT-/EXT- literal")


def _call_sels(
    raw: Any,
    where: str,
    tools: set[str],
    loans: dict[str, str],
    contacts: dict[str, str],
) -> tuple[CallSel, ...]:
    if not isinstance(raw, list):
        raise AttackFileError(where, "must be a list of tables")
    out = []
    for i, entry in enumerate(raw):
        w = f"{where}[{i}]"
        data = _only(entry, SEL_KEYS, w)
        tool = _str(data, "tool", w)
        if tool not in tools:
            raise AttackFileError(w, f"unknown tool {tool!r}")
        refs: dict[str, str | None] = {"loan": None, "recipient": None}
        for kind in refs:
            if kind in data:
                try:
                    refs[kind] = resolve_ref(data[kind], kind, loans, contacts)
                except ValueError as exc:
                    raise AttackFileError(f"{w}.{kind}", str(exc)) from None
        extra = data.get("extra_arg")
        if extra is not None and (not isinstance(extra, str) or not extra):
            raise AttackFileError(f"{w}.extra_arg", "must be a non-empty string")
        out.append(CallSel(tool, refs["loan"], refs["recipient"], extra))
    return tuple(out)


def _tamper(raw: Any, where: str, loans: dict[str, str]) -> Tamper:
    data = _only(raw, TAMPER_KEYS, where)
    loan, fld, marker, text = (_str(data, k, where) for k in ("loan", "field", "marker", "text"))
    if loan not in loans:
        raise AttackFileError(where, f"unknown loan selector {loan!r}")
    if fld not in TAMPER_FIELDS:
        raise AttackFileError(where, f"unknown field {fld!r}")
    if marker not in text:
        raise AttackFileError(where, "the marker does not occur in the text")
    return Tamper(loans[loan], fld, marker, text)


def _spec_patch(raw: Any, where: str, real: list[dict[str, Any]]) -> SpecPatch:
    data = _only(raw, PATCH_KEYS, where)
    kind, tool = _str(data, "kind", where), _str(data, "tool", where)
    if kind not in PATCH_KINDS:
        raise AttackFileError(where, f"unknown kind {kind!r}")
    relock = data.get("relock", False)
    if not isinstance(relock, bool):
        raise AttackFileError(where, "relock must be true or false")
    text, param = data.get("text"), data.get("param")
    if kind == "add_tool":
        if tool in specs.TOOL_NAMES:
            raise AttackFileError(where, f"add_tool names an existing tool {tool!r}")
    elif tool not in specs.TOOL_NAMES:
        raise AttackFileError(where, f"unknown tool {tool!r}")
    if kind in ("description_append", "add_tool"):
        _str(data, "text", where)
        if param is not None:
            raise AttackFileError(where, f"{kind} takes no param")
    else:  # drop_pattern
        param = _str(data, "param", where)
        if text is not None:
            raise AttackFileError(where, "drop_pattern takes no text")
        props = next(
            s["function"]["parameters"]["properties"] for s in real if s["function"]["name"] == tool
        )
        if "pattern" not in props.get(param, {}):
            raise AttackFileError(where, f"{tool}.{param} has no pattern")
    return SpecPatch(kind, tool, text, param, relock)


def _script(raw: Any, where: str, tools: set[str]) -> tuple[tuple[dict[str, Any], ...], ...]:
    if not isinstance(raw, list) or not raw:
        raise AttackFileError(where, "must be a non-empty list of messages")
    out = []
    for i, message in enumerate(raw):
        w = f"{where}[{i}]"
        if not isinstance(message, list) or not 1 <= len(message) <= MAX_CALLS_PER_MESSAGE:
            raise AttackFileError(w, f"a message holds 1 to {MAX_CALLS_PER_MESSAGE} calls")
        calls = []
        for j, call in enumerate(message):
            data = _only(call, CALL_KEYS, f"{w}[{j}]")
            tool = _str(data, "tool", f"{w}[{j}]")
            if tool not in tools:
                raise AttackFileError(f"{w}[{j}]", f"unknown tool {tool!r}")
            if not isinstance(data.get("args"), dict):
                raise AttackFileError(f"{w}[{j}]", "args must be a table")
            calls.append({"tool": tool, "args": dict(data["args"])})
        out.append(tuple(calls))
    return tuple(out)


def _expect(raw: Any, where: str) -> dict[str, Expect]:
    data = _only(raw, {"default", *CONFIGS}, where)
    if "default" not in data:
        raise AttackFileError(where, "default is required")
    parsed: dict[str, Expect] = {}
    for key, entry in data.items():
        w = f"{where}.{key}"
        e = _only(entry, EXPECT_ENTRY_KEYS, w)
        if "out" not in e or "hit" not in e:
            raise AttackFileError(w, "out and hit are required")
        if not isinstance(e["out"], list) or not all(isinstance(v, str) for v in e["out"]):
            raise AttackFileError(f"{w}.out", "must be a list of strings")
        for value in e["out"]:
            if not valid_out(value):
                raise AttackFileError(f"{w}.out", f"unknown outcome {value!r}")
        if not isinstance(e["hit"], list) or any(v not in TARGETS for v in e["hit"]):
            raise AttackFileError(f"{w}.hit", "must be a list of T1..T6")
        parsed[key] = Expect(tuple(e["out"]), tuple(sorted(e["hit"])))
    return {c: parsed.get(c, parsed["default"]) for c in CONFIGS}


def _attack(raw: Any, ctx: dict[str, Any]) -> Attack:
    attack_id = raw.get("id") if isinstance(raw, dict) else None
    if not isinstance(attack_id, str) or not attack_id:
        raise AttackFileError("attacks[?]", "id is required")
    where = f"attacks[{attack_id}]"
    data = _only(raw, ATTACK_KEYS, where)
    for key in ATTACK_REQUIRED:
        if key not in data:
            raise AttackFileError(where, f"{key} is required")
    loans, contacts = ctx["loans"], ctx["contacts"]
    channel, user = _str(data, "channel", where), _str(data, "user", where)
    if channel not in CHANNELS:
        raise AttackFileError(f"{where}.channel", f"unknown channel {channel!r}")
    if user not in ctx["users"]:
        raise AttackFileError(f"{where}.user", f"unknown user {user!r}")
    for ch, key in CHANNEL_FIELD.items():
        if (ch == channel) != (key in data):
            raise AttackFileError(where, f"{key} is required for, and only for, channel {ch}")

    template = _str(data, "question", where)
    names: dict[str, str] = {**loans, **contacts}
    for _, name, _, _ in string.Formatter().parse(template):
        if name is not None and name not in names:
            raise AttackFileError(f"{where}.question", f"undefined placeholder {{{name}}}")
    question = template.format(**names)

    doc = data.get("doc")
    if doc is not None:
        if not isinstance(doc, str) or not doc_path(doc, ctx["adv_dir"]).is_file():
            raise AttackFileError(f"{where}.doc", f"no such document {doc!r}")
        if instruction_chunk(doc, ctx["adv_dir"]) is None:
            raise AttackFileError(f"{where}.doc", f"{doc} has no instruction line")
    gateway = data.get("expect_gateway")
    if gateway is not None and (not isinstance(gateway, str) or not GATEWAY_RE.fullmatch(gateway)):
        raise AttackFileError(f"{where}.expect_gateway", "must look like R10")
    matrix_only = data.get("matrix_only", False)
    if not isinstance(matrix_only, bool):
        raise AttackFileError(f"{where}.matrix_only", "must be true or false")

    tamper = _tamper(data["tamper"], f"{where}.tamper", loans) if "tamper" in data else None
    patch = (
        _spec_patch(data["spec_patch"], f"{where}.spec_patch", ctx["real_specs"])
        if "spec_patch" in data
        else None
    )
    tools = set(specs.TOOL_NAMES)
    if patch is not None and patch.kind == "add_tool":
        tools.add(patch.tool)

    signature = _call_sels(data["signature"], f"{where}.signature", tools, loans, contacts)
    if not signature:
        raise AttackFileError(f"{where}.signature", "must not be empty")
    requested = _call_sels(data.get("requested", []), f"{where}.requested", tools, loans, contacts)
    targets = _str_list(data["targets"], f"{where}.targets")
    if any(t not in TARGETS for t in targets):
        raise AttackFileError(f"{where}.targets", "must be a list of T1..T6")
    config_write = _str_list(data.get("config_write", []), f"{where}.config_write")
    if any(c not in CONFIGS for c in config_write):
        raise AttackFileError(f"{where}.config_write", "names an unknown config")

    return Attack(
        id=attack_id,
        channel=channel,
        user=user,
        question=question,
        question_template=template,
        signature=signature,
        requested=requested,
        targets=tuple(targets),
        script=_script(data["script"], f"{where}.script", tools),
        expect=_expect(data["expect"], f"{where}.expect"),
        doc=doc,
        expect_gateway=gateway,
        tamper=tamper,
        spec_patch=patch,
        matrix_only=matrix_only,
        config_write=tuple(config_write),
    )


def _check_literals(adv_dir: Path, attacks: list[Attack], known: set[str]) -> None:
    for path in sorted(adv_dir.glob("*.md")):
        for lit in _literals(path.read_text(encoding="utf-8")):
            if lit not in known:
                raise AttackFileError(f"corpus/adversarial/{path.name}", f"stray literal {lit}")
    for attack in attacks:
        if attack.tamper is not None:
            for lit in _literals(attack.tamper.text):
                if lit not in known:
                    raise AttackFileError(f"attacks[{attack.id}].tamper", f"stray literal {lit}")


def _check_docs(adv_dir: Path) -> None:
    for path in sorted(adv_dir.glob("*.md")):
        try:
            doc = parse_doc(path)
        except (OSError, ValueError) as exc:
            raise AttackFileError(f"corpus/adversarial/{path.name}", str(exc)) from None
        if doc.front_matter.get("id") != path.stem or not doc.sections:
            raise AttackFileError(f"corpus/adversarial/{path.name}", "id or sections missing")


# --- entry point -------------------------------------------------------------------------------


def load_attacks(
    path: str | Path | None = None,
    *,
    adv_dir: str | Path | None = None,
    facts: Facts | None = None,
    policy: AgentPolicy | None = None,
    users: dict[str, mockdata.User] | None = None,
    loans: list[dict[str, Any]] | None = None,
    contacts: list[dict[str, Any]] | None = None,
) -> AttackSet:
    path = Path(path or ATTACKS_PATH)
    adv = Path(adv_dir or ADV_DIR)
    raw_bytes = path.read_bytes()
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise AttackFileError("file", f"not valid TOML ({exc})") from None
    facts = facts or load_facts()
    policy = policy or load_policy(env={})
    users = users if users is not None else mockdata.load_users()
    loan_rows = loans if loans is not None else mockdata.load_loans()
    contact_rows = contacts if contacts is not None else mockdata.load_contacts()

    _only(data, TOP_KEYS, "file")
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise AttackFileError("version", "must be an integer")
    registered = _registered(data.get("registered"))

    resolved: dict[str, str] = {}
    raw_loans = data.get("loans", {})
    names = set(raw_loans) if isinstance(raw_loans, dict) else set()
    for name, raw in _only(raw_loans, names, "loans").items():
        try:
            sel = _parse_selector(name, raw, users)
        except TaskFileError as exc:
            raise AttackFileError(exc.where, exc.what) from None
        found = resolve_selector(sel, users, loan_rows, policy, facts)
        if found is None:
            raise AttackFileError(f"loans.{name}", "no loan matches")
        if found != sel.expect:
            raise AttackFileError(f"loans.{name}", f"resolves to {found}, expected {sel.expect}")
        resolved[name] = found
    contact_map = _contacts(data.get("contacts", {}), resolved, contact_rows)

    _check_docs(adv)
    if not isinstance(data.get("attacks"), list):
        raise AttackFileError("attacks", "must be an array of tables")
    ctx = {
        "users": users,
        "loans": resolved,
        "contacts": contact_map,
        "adv_dir": adv,
        "real_specs": specs.load_specs(),
    }
    attacks: list[Attack] = []
    seen: set[str] = set()
    for raw in data["attacks"]:
        attack = _attack(raw, ctx)
        if attack.id in seen:
            raise AttackFileError(f"attacks[{attack.id}]", "duplicate id")
        seen.add(attack.id)
        attacks.append(attack)
    _check_literals(adv, attacks, set(resolved.values()) | set(contact_map.values()))

    return AttackSet(
        path=path,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        version=version,
        registered=registered,
        loans=resolved,
        contacts=contact_map,
        attacks=attacks,
        adv_dir=adv,
    )
