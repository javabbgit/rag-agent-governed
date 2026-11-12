"""Retrieval eval set: loading and checking eval/retrieval/questions.toml. Pure: no network, no DB.

Chunk truth comes from the corpus files (corpus.iter_chunks and each doc's front matter), never
from the database. Patterns that already live in scripts/check_corpus.py (PII, banned terms) and
the smoke queries are passed in by the caller (`Rules`), not copied here.
"""

from __future__ import annotations

import re
import tomllib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rag_agent.bm25 import tokenize
from rag_agent.corpus import Section, iter_chunks, parse_doc

FAIL = "FAIL"
WARN = "WARN"

LEVEL_PER_QUESTION = "per_question"
LEVEL_DEFAULT = "default"
LEVEL_FINAL = "final"

SPLITS = ("dev", "test")
KINDS = ("answerable", "oos")
ANSWERABLE_CATEGORIES = (
    "exact",
    "paraphrase",
    "confusable",
    "superseded_trap",
    "fine_grained",
    "table",
    "multi_doc",
)
OOS_CATEGORIES = ("oos_far", "oos_near")
PERSONAS = ("borrower", "broker", "staff")

COMMON_FIELDS: dict[str, type] = {
    "id": str,
    "split": str,
    "kind": str,
    "category": str,
    "persona": str,
    "query": str,
    "answer": str,
    "gold": list,
    "related": list,
    "nonrel": list,
    "origin": str,
    "reviewed": bool,
}

# id ranges: split -> (prefix, answerable max, oos max)
ID_RANGES = {"dev": ("D", 30, 38), "test": ("T", 50, 62)}
DEV_TOPICS_FAR = ("car loans", "travel insurance", "cryptocurrency", "IT password resets")
DEV_TOPICS_NEAR = (
    "reverse mortgages",
    "blend-and-extend",
    "cottage or recreational properties",
    "co-signers and guarantors",
)
TEST_QUOTAS = {
    "exact": 8,
    "paraphrase": 10,
    "confusable": 8,
    "superseded_trap": 6,
    "fine_grained": 6,
    "table": 6,
    "multi_doc": 6,
}
CONFUSABLE_DOCS = {"PRD-001", "PRD-002", "PRD-003", "PRD-004", "PRD-006", "PRD-007"}
TRAP_DOCS = {"POL-003", "POL-009", "RATE-001"}
DOC_ID_RE = re.compile(r"\b(POL|PRC|PRD|RATE|FAQ)-\d{3}\b", re.IGNORECASE)
LOAN_PREFIX_RE = re.compile(r"\bDMC-", re.IGNORECASE)
SMOKE_ORIGIN_RE = re.compile(r"^smoke-Q(0[1-9]|1[0-2])$")
MAX_GOLD_DOC_WARN = 6
JACCARD_WARN = 0.7


@dataclass(frozen=True)
class Finding:
    level: str
    qid: str
    msg: str


@dataclass
class Truth:
    chunks: dict[str, Section]
    doc_status: dict[str, str]
    doc_h2: dict[str, int]

    def status_of(self, chunk_id: str) -> str:
        return self.doc_status.get(self.chunks[chunk_id].doc_id, "current")

    def current_docs(self) -> set[str]:
        return {d for d, s in self.doc_status.items() if s == "current"}


@dataclass
class Rules:
    """Caller-supplied patterns (from scripts/check_corpus.py and scripts/retrieval_smoke.py)."""

    pii: dict[str, re.Pattern[str]] = field(default_factory=dict)
    banned: list[re.Pattern[str]] = field(default_factory=list)
    smoke: dict[str, str] = field(default_factory=dict)  # "Q01" -> query


@dataclass
class Result:
    findings: list[Finding]
    counts: dict[str, dict[str, int]]  # split -> kind -> n

    @property
    def n_fail(self) -> int:
        return sum(f.level == FAIL for f in self.findings)

    @property
    def n_warn(self) -> int:
        return sum(f.level == WARN for f in self.findings)

    def summary(self) -> str:
        c = self.counts
        return (
            f"evalset: dev {c['dev']['answerable']}+{c['dev']['oos']}, "
            f"test {c['test']['answerable']}+{c['test']['oos']}, "
            f"FAIL {self.n_fail}, WARN {self.n_warn}"
        )


def load(path: str | Path) -> dict[str, Any]:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def questions(data: dict[str, Any]) -> list[dict[str, Any]]:
    return list(data.get("q", []))


def split_questions(data: dict[str, Any], split: str) -> list[dict[str, Any]]:
    return sorted((q for q in questions(data) if q.get("split") == split), key=lambda q: q["id"])


def build_truth(docs_dir: str | Path) -> Truth:
    chunks = {c.chunk_id: c for c in iter_chunks(docs_dir)}
    status: dict[str, str] = {}
    h2: dict[str, int] = {}
    for path in sorted(Path(docs_dir).glob("*.md"), key=lambda p: p.stem):
        doc = parse_doc(path)
        doc_id = str(doc.front_matter.get("id", path.stem))
        status[doc_id] = str(doc.front_matter.get("status", "current"))
        h2[doc_id] = len(doc.h2_headings)
    return Truth(chunks, status, h2)


def norm(text: str) -> str:
    return " ".join(text.lower().split())


def _words(query: str) -> int:
    return len(query.split())


def _id_parts(qid: str) -> tuple[str, int] | None:
    m = re.fullmatch(r"([DT])(\d{2})", qid)
    return (m.group(1), int(m.group(2))) if m else None


# --- per-question rules ------------------------------------------------------------------------


def _schema(q: dict[str, Any], add: Any) -> bool:
    qid = q.get("id", "?")
    fields = dict(COMMON_FIELDS)
    if q.get("kind") == "oos":
        fields["topic"] = str
    ok = True
    for name in fields:
        if name not in q:
            add(FAIL, qid, f"schema: missing field {name!r}")
            ok = False
    for name in q:
        if name not in fields:
            add(FAIL, qid, f"schema: extra field {name!r}")
            ok = False
    for name, typ in fields.items():
        if name in q:
            v = q[name]
            good = isinstance(v, bool) if typ is bool else isinstance(v, typ)
            if typ is str and isinstance(v, bool):
                good = False
            if typ is list:
                good = isinstance(v, list) and all(isinstance(x, str) for x in v)
            if not good:
                add(FAIL, qid, f"schema: field {name!r} has the wrong type")
                ok = False
    if not ok:
        return False
    if q["split"] not in SPLITS:
        add(FAIL, qid, f"schema: split {q['split']!r} not in {SPLITS}")
        ok = False
    if q["kind"] not in KINDS:
        add(FAIL, qid, f"schema: kind {q['kind']!r} not in {KINDS}")
        ok = False
    if q["persona"] not in PERSONAS:
        add(FAIL, qid, f"schema: persona {q['persona']!r} not in {PERSONAS}")
        ok = False
    if not ok:
        return False
    cats = ANSWERABLE_CATEGORIES if q["kind"] == "answerable" else OOS_CATEGORIES
    if q["category"] not in cats:
        add(FAIL, qid, f"schema: category {q['category']!r} not valid for kind {q['kind']}")
        ok = False
    origin = q["origin"]
    if origin != "new" and not SMOKE_ORIGIN_RE.match(origin):
        add(FAIL, qid, f"schema: origin {origin!r} is not 'new' or 'smoke-Q01'..'smoke-Q12'")
        ok = False
    elif q["split"] == "test" and origin != "new":
        add(FAIL, qid, "schema: a test question must have origin 'new'")
        ok = False
    parts = _id_parts(qid)
    prefix, n_ans, n_oos = ID_RANGES[q["split"]]
    if parts is None or parts[0] != prefix:
        add(FAIL, qid, f"schema: id does not match split {q['split']} (expected {prefix}NN)")
        ok = False
    else:
        n = parts[1]
        lo, hi = (1, n_ans) if q["kind"] == "answerable" else (n_ans + 1, n_oos)
        if not lo <= n <= hi:
            add(FAIL, qid, f"schema: id number outside {prefix}{lo:02d}-{prefix}{hi:02d} for kind")
            ok = False
    return ok


def _check_query(q: dict[str, Any], rules: Rules, add: Any) -> None:
    qid, query = q["id"], q["query"]
    if not query.isascii():
        add(FAIL, qid, "query: non-ASCII characters")
    if not query.rstrip().endswith("?"):
        add(FAIL, qid, "query: does not end with '?'")
    n = _words(query)
    if n < 5 or n > 30:
        add(FAIL, qid, f"query: {n} words (need 5 to 30)")
    for name, rx in rules.pii.items():
        if rx.search(query):
            add(FAIL, qid, f"query: PII pattern ({name})")
    if LOAN_PREFIX_RE.search(query):
        add(FAIL, qid, "query: contains a loan number")
    m = DOC_ID_RE.search(query)
    if m:
        add(FAIL, qid, f"query: contains a document id {m.group(0)!r}")
    origin = q["origin"]
    if SMOKE_ORIGIN_RE.match(origin):
        expected = rules.smoke.get(origin.removeprefix("smoke-"))
        if expected is None or q["query"] != expected:
            add(FAIL, qid, f"smoke: query differs from the {origin} constant")
    if q["split"] == "test" and norm(query) in {norm(s) for s in rules.smoke.values()}:
        add(FAIL, qid, "smoke: test query equals a smoke query")


def answer_in(answer: str, texts: Iterable[str]) -> bool:
    a = norm(answer)
    return bool(a) and any(a in norm(t) for t in texts)


def _check_answerable(q: dict[str, Any], truth: Truth, rules: Rules, add: Any) -> None:
    qid = q["id"]
    gold, related, nonrel = q["gold"], q["related"], q["nonrel"]
    if not gold:
        add(FAIL, qid, "answerable: gold is empty")
    missing = False
    for name in ("gold", "related", "nonrel"):
        for cid in q[name]:
            if cid not in truth.chunks:
                add(FAIL, qid, f"answerable: {name} chunk {cid!r} does not exist")
                missing = True
    for name in ("gold", "related"):
        for cid in q[name]:
            if cid in truth.chunks and truth.status_of(cid) == "superseded":
                add(FAIL, qid, f"answerable: superseded chunk {cid!r} in {name}")
    sets = {"gold": set(gold), "related": set(related), "nonrel": set(nonrel)}
    for a, b in (("gold", "related"), ("gold", "nonrel"), ("related", "nonrel")):
        both = sets[a] & sets[b]
        if both:
            add(FAIL, qid, f"answerable: {a} and {b} overlap on {sorted(both)}")
    if any(
        len(v) != len(sets[k])
        for k, v in (("gold", gold), ("related", related), ("nonrel", nonrel))
    ):
        add(FAIL, qid, "answerable: duplicate chunk id inside a list")
    answer = q["answer"]
    gold_chunks = [truth.chunks[c] for c in gold if c in truth.chunks]
    if not answer.strip():
        add(FAIL, qid, "answerable: answer is empty")
    else:
        if not missing and gold_chunks and not answer_in(answer, (c.text for c in gold_chunks)):
            add(FAIL, qid, "answerable: answer not found in any gold chunk")
        if norm(answer) in norm(q["query"]):
            add(FAIL, qid, "answerable: answer appears in the query")
    for rx in rules.banned:
        m = rx.search(q["query"])
        if m:
            add(FAIL, qid, f"answerable: query contains banned term {m.group(0)!r}")
    if not missing and gold_chunks:
        _check_category(q, gold_chunks, truth, add)


def _check_category(q: dict[str, Any], gold_chunks: list[Section], truth: Truth, add: Any) -> None:
    qid, cat, answer = q["id"], q["category"], q["answer"]
    docs = {c.doc_id for c in gold_chunks}
    if cat == "paraphrase":
        qt = set(tokenize(q["query"]))
        shared = qt & {t for c in gold_chunks for t in tokenize(c.heading)}
        if shared:
            add(FAIL, qid, f"paraphrase: query shares heading tokens {sorted(shared)}")
    elif cat == "confusable":
        bad = docs - CONFUSABLE_DOCS
        if bad:
            add(FAIL, qid, f"confusable: gold docs outside the product set: {sorted(bad)}")
    elif cat == "superseded_trap":
        bad = docs - TRAP_DOCS
        if bad:
            add(FAIL, qid, f"superseded_trap: gold docs outside {sorted(TRAP_DOCS)}: {sorted(bad)}")
        for d in sorted(docs & TRAP_DOCS):
            v1 = [c.text for c in truth.chunks.values() if c.doc_id == f"{d}-V1"]
            if not v1:
                add(FAIL, qid, f"superseded_trap: no {d}-V1 document in the corpus")
            elif answer_in(answer, v1):
                add(FAIL, qid, f"superseded_trap: answer also found in {d}-V1")
    elif cat == "fine_grained":
        if len(gold_chunks) != 1:
            add(FAIL, qid, "fine_grained: needs exactly one gold chunk")
        elif truth.doc_h2.get(gold_chunks[0].doc_id, 0) < 5:
            add(FAIL, qid, f"fine_grained: {gold_chunks[0].doc_id} has fewer than 5 H2 sections")
    elif cat == "table":
        lines = (
            ln for c in gold_chunks for ln in c.text.splitlines() if ln.lstrip().startswith("|")
        )
        if not answer_in(answer, lines):
            add(FAIL, qid, "table: answer not found in a table line of a gold chunk")
    elif cat == "multi_doc" and len(docs) < 2:
        add(FAIL, qid, "multi_doc: gold chunks come from fewer than 2 docs")


def _check_oos(q: dict[str, Any], manifest: dict[str, Any], add: Any) -> None:
    qid = q["id"]
    if q["gold"] or q["related"]:
        add(FAIL, qid, "oos: gold and related must be empty")
    if q["answer"] != "":
        add(FAIL, qid, "oos: answer must be empty")
    oos = manifest.get("out_of_scope", {})
    key = "topics_far" if q["category"] == "oos_far" else "topics_near"
    allowed = {t.lower() for t in oos.get(key, [])}
    if q["topic"].lower() not in allowed:
        add(FAIL, qid, f"oos: topic {q['topic']!r} not in the manifest's {key}")


# --- count rules -------------------------------------------------------------------------------


def _count_rules(split: str, qs: list[dict[str, Any]], manifest: dict[str, Any], add: Any) -> None:
    ans = [q for q in qs if q["kind"] == "answerable"]
    oos = [q for q in qs if q["kind"] == "oos"]
    cats = Counter(q["category"] for q in ans)
    if split == "dev":
        if len(ans) != 30:
            add(FAIL, "dev", f"count: {len(ans)} answerable (need 30)")
        for c in ANSWERABLE_CATEGORIES:
            if cats[c] < 3:
                add(FAIL, "dev", f"count: category {c} has {cats[c]} (need at least 3)")
        for i in range(1, 13):
            q = next((x for x in qs if x["id"] == f"D{i:02d}"), None)
            if q is None or q["origin"] != f"smoke-Q{i:02d}":
                add(FAIL, "dev", f"count: D{i:02d} must have origin smoke-Q{i:02d}")
        far_need, near_need = 4, 4
        far_topics = sorted(t.lower() for t in DEV_TOPICS_FAR)
        near_topics = sorted(t.lower() for t in DEV_TOPICS_NEAR)
    else:
        if len(ans) != 50:
            add(FAIL, "test", f"count: {len(ans)} answerable (need 50)")
        for c, n in TEST_QUOTAS.items():
            if cats[c] != n:
                add(FAIL, "test", f"count: category {c} has {cats[c]} (need exactly {n})")
        personas = Counter(q["persona"] for q in ans)
        for p in PERSONAS:
            if personas[p] < 8:
                add(FAIL, "test", f"count: persona {p} has {personas[p]} (need at least 8)")
        far_need, near_need = 6, 6
        o = manifest.get("out_of_scope", {})
        dev_far = {t.lower() for t in DEV_TOPICS_FAR}
        dev_near = {t.lower() for t in DEV_TOPICS_NEAR}
        far_topics = sorted(t.lower() for t in o.get("topics_far", []) if t.lower() not in dev_far)
        near_topics = sorted(
            t.lower() for t in o.get("topics_near", []) if t.lower() not in dev_near
        )
    far = [q for q in oos if q["category"] == "oos_far"]
    near = [q for q in oos if q["category"] == "oos_near"]
    if len(oos) != far_need + near_need or len(far) != far_need or len(near) != near_need:
        add(
            FAIL,
            split,
            f"count: oos {len(far)} far + {len(near)} near (need {far_need} + {near_need})",
        )
    for label, group, expected in (("far", far, far_topics), ("near", near, near_topics)):
        got = sorted(q["topic"].lower() for q in group)
        if got != expected:
            add(FAIL, split, f"topics: oos_{label} topics {got} must be exactly {expected}")


def _final_rules(qs: list[dict[str, Any]], truth: Truth, add: Any) -> None:
    for q in qs:
        if q["reviewed"] is not True:
            add(FAIL, q["id"], "final: reviewed is not true")
    use: Counter[str] = Counter()
    for q in qs:
        if q["kind"] == "answerable":
            use.update({truth.chunks[c].doc_id for c in q["gold"] if c in truth.chunks})
    for d in sorted(truth.current_docs()):
        if use[d] == 0:
            add(FAIL, d, "coverage: current doc is the gold doc of no answerable question")
        elif use[d] > MAX_GOLD_DOC_WARN:
            add(WARN, d, f"coverage: doc is a gold doc of {use[d]} questions (more than 6)")


# --- entry point -------------------------------------------------------------------------------


def check(
    data: dict[str, Any],
    truth: Truth,
    manifest: dict[str, Any],
    rules: Rules,
    level: str = LEVEL_DEFAULT,
) -> Result:
    findings: list[Finding] = []

    def add(lv: str, qid: str, msg: str) -> None:
        findings.append(Finding(lv, qid, msg))

    for key in data:
        if key not in ("version", "q"):
            add(FAIL, "file", f"schema: unknown top-level key {key!r}")
    if data.get("version") != 1:
        add(FAIL, "file", "schema: version must be 1")

    qs = questions(data)
    seen_ids: dict[str, int] = {}
    seen_queries: dict[str, str] = {}
    valid: list[dict[str, Any]] = []
    counts = {s: dict.fromkeys(KINDS, 0) for s in SPLITS}
    for q in qs:
        qid = q.get("id")
        if isinstance(qid, str):
            if qid in seen_ids:
                add(FAIL, qid, "schema: duplicate id")
            seen_ids[qid] = seen_ids.get(qid, 0) + 1
        if q.get("split") in counts and q.get("kind") in KINDS:
            counts[q["split"]][q["kind"]] += 1
        if not _schema(q, add):
            continue
        valid.append(q)
        _check_query(q, rules, add)
        key = norm(q["query"])
        if key in seen_queries:
            add(FAIL, q["id"], f"query: duplicate of {seen_queries[key]}")
        else:
            seen_queries[key] = q["id"]
        if q["kind"] == "answerable":
            _check_answerable(q, truth, rules, add)
        else:
            _check_oos(q, manifest, add)

    order = [(0 if q["split"] == "dev" else 1, q["id"]) for q in valid]
    if order != sorted(order):
        add(FAIL, "file", "schema: questions must be ordered dev then test, by id")

    dev_q = [q for q in valid if q["split"] == "dev"]
    for q in valid:
        if q["split"] != "test":
            continue
        tq = set(tokenize(q["query"]))
        for d in dev_q:
            dq = set(tokenize(d["query"]))
            union = tq | dq
            if union and len(tq & dq) / len(union) >= JACCARD_WARN:
                add(WARN, q["id"], f"query: token Jaccard >= 0.7 with dev query {d['id']}")
                break

    if level in (LEVEL_DEFAULT, LEVEL_FINAL):
        for split in SPLITS:
            sq = [q for q in valid if q["split"] == split]
            if sq or level == LEVEL_FINAL:
                _count_rules(split, sq, manifest, add)
    if level == LEVEL_FINAL:
        _final_rules(valid, truth, add)

    return Result(findings, counts)


def gold_docs(q: dict[str, Any], truth: Truth) -> set[str]:
    return {truth.chunks[c].doc_id for c in q["gold"] if c in truth.chunks}
