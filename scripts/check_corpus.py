"""Corpus checker: structure, front matter, PII patterns, banned terms, fact consistency.

Exit code 1 on any FAIL. WARN findings never fail the run. No network.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rag_agent.corpus import Doc, load_facts, load_manifest, parse_text, slugify

REPO_ROOT = Path(__file__).resolve().parent.parent
FAIL, WARN = "FAIL", "WARN"

REQUIRED_FM = (
    "id",
    "title",
    "doc_type",
    "version",
    "status",
    "effective_date",
    "owner",
    "audience",
    "tags",
)
DOC_TYPES = {"policy", "process", "product", "rate_sheet", "faq"}
STATUSES = {"current", "superseded"}
AUDIENCES = {"staff", "broker", "borrower"}
ID_RE = re.compile(r"^(?:POL|PRC|PRD|RATE|FAQ)-\d{3}(?:-V1)?$")

SUMMARY_MAX_WORDS = 60
H2_MIN, H2_MAX = 3, 8
H2_WORDS_MIN, H2_WORDS_MAX = 120, 350
GENERIC_H2 = {
    "overview",
    "introduction",
    "details",
    "limits",
    "general",
    "summary",
    "notes",
    "other",
}

# Real organizations must not appear in the corpus (case-insensitive, whole words).
REAL_ORGS = (
    "OSFI",
    "B-20",
    "CMHC",
    "Sagen",
    "Canada Guaranty",
    "Equifax",
    "TransUnion",
    "FSRA",
    "FCAC",
    "OBSI",
    "RBC",
    "TD Bank",
    "Scotiabank",
    "BMO",
    "CIBC",
    "First National",
)

LOAN_RE = re.compile(r"\bDMC-\d{4}-\d{5}\b")
BAD_LOAN_RE = re.compile(r"\bDMC-\d")
PII_PATTERNS = {
    "9-digit number": re.compile(r"\b\d{3}[ -]?\d{3}[ -]?\d{3}\b"),
    "long digit run": re.compile(r"\d(?:[ -]?\d){9,}"),
    "email address": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "phone number": re.compile(r"\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}"),
    "postal code": re.compile(r"[A-Za-z]\d[A-Za-z] ?\d[A-Za-z]\d"),
    "street address": re.compile(
        r"\b\d+\s+[A-Z][A-Za-z]*\s+"
        r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Way|Crescent|Court)\b"
    ),
    "URL": re.compile(r"https?://|\bwww\.", re.IGNORECASE),
}
ORG_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(o) for o in REAL_ORGS) + r")(?!\w)", re.IGNORECASE
)
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.IGNORECASE)
PTS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:points|pts)\b", re.IGNORECASE)
DOLLAR_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
DAYS_RE = re.compile(r"\b(\d+)[ -](?:business |calendar )?days?\b", re.IGNORECASE)
NUM_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*")
SUPERSEDED_RE = re.compile(
    r"^Superseded on (\d{4}-\d{2}-\d{2}) by ([A-Z]+-\d{3}) version \d+(?:\.\d+)*\.(?:\s|$)"
)


@dataclass
class Finding:
    level: str
    doc_id: str
    msg: str


@dataclass
class DocReport:
    doc_id: str
    sections: int = 0
    words: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(f.level == FAIL for f in self.findings)


@dataclass
class Context:
    facts: dict[str, Any]
    manifest: dict[str, Any]
    ids: set[str]
    by_id: dict[str, dict[str, Any]]
    banned: list[re.Pattern[str]]
    allowed_pct: set[float]
    allowed_pct_v1: set[float]
    fact_numbers: set[float]
    fact_numbers_v1: set[float]


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _flatten(tbl: dict[str, Any], skip_v1: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for sec, vals in tbl.items():
        if sec == "v1" and skip_v1:
            continue
        for k, v in vals.items():
            out[f"{sec}.{k}"] = v
    return out


def _r(x: float) -> float:
    return round(abs(float(x)), 2)


def _allowed_from(facts: dict[str, Any], rates_src: dict[str, Any], buffer: float) -> set[float]:
    """Numbers allowed as % / points: facts, prime + spreads, rates + buffer."""
    flat = _flatten(facts)
    allowed = {0.0, 100.0}
    allowed |= {_r(v) for v in flat.values() if _num(v)}
    prime = rates_src["prime_pct"]
    derived: list[float] = []
    for k, v in rates_src.items():
        if not _num(v) or k == "prime_pct":
            continue
        if k.endswith("_spread_pts"):
            derived.append(prime + v)
            allowed.add(_r(prime + v))
        elif k.endswith("_pct"):
            derived.append(v)
    allowed |= {_r(d + buffer) for d in derived}
    return allowed


def build_context(facts: dict[str, Any], manifest: dict[str, Any]) -> Context:
    buffer = facts["qualifying"]["buffer_pts"]
    floor = facts["qualifying"]["floor_pct"]
    cur = _allowed_from(facts, facts["rates"], buffer) | {_r(floor)}
    v1_facts = facts.get("v1", {})
    v1_flat = _flatten(v1_facts, skip_v1=False) if v1_facts else {}
    rates_v1 = {**facts["rates"], **v1_facts.get("rates", {})}
    v1 = cur | {_r(v) for v in v1_flat.values() if _num(v)}
    v1 |= _allowed_from(facts, rates_v1, buffer)
    nums = {_r(v) for v in _flatten(facts).values() if _num(v)}
    nums_v1 = nums | {_r(v) for v in v1_flat.values() if _num(v)}
    docs = manifest["docs"]
    banned = [
        re.compile(r"\b" + re.escape(t), re.IGNORECASE)
        for t in manifest.get("out_of_scope", {}).get("banned_terms", [])
    ]
    return Context(
        facts=facts,
        manifest=manifest,
        ids={d["id"] for d in docs},
        by_id={d["id"]: d for d in docs},
        banned=banned,
        allowed_pct=cur,
        allowed_pct_v1=v1,
        fact_numbers=nums,
        fact_numbers_v1=nums_v1,
    )


def _first_sentence_block(doc: Doc) -> str:
    """Body text after the H1, up to the first H2."""
    return doc.sections[0].text if doc.sections and doc.sections[0].heading == "summary" else ""


def _check_front_matter(
    doc: Doc, stem: str, ctx: Context, add: Any, entry: dict[str, Any] | None
) -> None:
    fm = doc.front_matter
    missing = [k for k in REQUIRED_FM if k not in fm]
    if missing:
        add(FAIL, f"front matter lacks: {', '.join(missing)}")
    doc_id = fm.get("id")
    if "id" in fm and not (isinstance(doc_id, str) and ID_RE.match(doc_id)):
        add(FAIL, f"id {doc_id!r} is outside its allowed format")
    if "doc_type" in fm and fm["doc_type"] not in DOC_TYPES:
        add(FAIL, f"doc_type {fm['doc_type']!r} not in {sorted(DOC_TYPES)}")
    if "status" in fm and fm["status"] not in STATUSES:
        add(FAIL, f"status {fm['status']!r} not in {sorted(STATUSES)}")
    if "version" in fm and not (isinstance(fm["version"], str) and fm["version"].strip()):
        add(FAIL, "version must be a non-empty string")
    if "effective_date" in fm and not isinstance(fm["effective_date"], dt.date):
        add(FAIL, "effective_date must be a TOML date")
    if "owner" in fm and not (isinstance(fm["owner"], str) and fm["owner"].strip()):
        add(FAIL, "owner must be a non-empty string")
    for key in ("audience", "tags"):
        v = fm.get(key)
        if key in fm and not (isinstance(v, list) and v and all(isinstance(x, str) for x in v)):
            add(FAIL, f"{key} must be a non-empty array of strings")
    aud = fm.get("audience")
    if isinstance(aud, list) and any(a not in AUDIENCES for a in aud if isinstance(a, str)):
        add(FAIL, f"audience values must be in {sorted(AUDIENCES)}")
    if "id" in fm and doc_id != stem:
        add(FAIL, f"id {doc_id!r} differs from file stem {stem!r}")
    if entry is None:
        return
    for key in ("id", "title", "doc_type", "status"):
        if key in fm and fm[key] != entry.get(key):
            add(FAIL, f"{key} {fm[key]!r} differs from manifest {entry.get(key)!r}")


def _check_supersession(doc: Doc, ctx: Context, add: Any) -> None:
    fm = doc.front_matter
    for key in ("supersedes", "superseded_by"):
        if key in fm and fm[key] not in ctx.ids:
            add(FAIL, f"{key} names a missing id {fm[key]!r}")
    if fm.get("status") != "superseded":
        return
    if "superseded_by" not in fm:
        add(FAIL, "superseded doc lacks superseded_by")
    m = SUPERSEDED_RE.match(_first_sentence_block(doc))
    if not m:
        add(FAIL, 'first body sentence is not "Superseded on <date> by <ID> version <v>."')
    elif "superseded_by" in fm and m.group(2) != fm["superseded_by"]:
        add(FAIL, f"first sentence names {m.group(2)} but superseded_by is {fm['superseded_by']!r}")


def _check_structure(doc: Doc, add: Any) -> None:
    fm = doc.front_matter
    if doc.h1_count != 1:
        add(FAIL, f"expected exactly one H1, found {doc.h1_count}")
    if doc.title is not None and "title" in fm and doc.title != fm["title"]:
        add(FAIL, f"H1 {doc.title!r} != title {fm['title']!r}")
    if doc.sections and doc.sections[0].heading == "summary":
        n = doc.sections[0].word_count
        if n > SUMMARY_MAX_WORDS:
            add(FAIL, f"summary has {n} words (max {SUMMARY_MAX_WORDS})")
    h2s = [s for s in doc.sections if s.heading != "summary"]
    if not H2_MIN <= len(h2s) <= H2_MAX:
        add(FAIL, f"{len(h2s)} H2 sections (need {H2_MIN} to {H2_MAX})")
    seen: set[str] = set()
    for s in h2s:
        if s.word_count < H2_WORDS_MIN or s.word_count > H2_WORDS_MAX:
            add(
                FAIL,
                f"section {s.heading!r} has {s.word_count} words "
                f"(need {H2_WORDS_MIN} to {H2_WORDS_MAX})",
            )
        slug = slugify(s.heading)
        if slug in seen:
            add(FAIL, f"duplicate H2 slug {slug!r}")
        seen.add(slug)
        if s.heading.strip().lower() in GENERIC_H2:
            add(FAIL, f"generic H2 heading {s.heading!r}")


def _line_of(raw: str, pos: int) -> int:
    return raw.count("\n", 0, pos) + 1


def _check_text_rules(raw: str, add: Any, ctx: Context) -> None:
    for i, ch in enumerate(raw):
        if ord(ch) > 127:
            add(FAIL, f"non-ASCII character U+{ord(ch):04X} at line {_line_of(raw, i)}")
            break
    masked = LOAN_RE.sub("DMC-LOAN", raw)
    for name, rx in PII_PATTERNS.items():
        for m in rx.finditer(masked):
            add(FAIL, f"PII pattern ({name}) at line {_line_of(masked, m.start())}")
            break
    m = BAD_LOAN_RE.search(masked)
    if m:
        add(FAIL, f"malformed loan number at line {_line_of(masked, m.start())}")
    for rx in ctx.banned:
        m = rx.search(raw)
        if m:
            add(FAIL, f"banned term {m.group(0)!r} at line {_line_of(raw, m.start())}")
    for m in ORG_RE.finditer(raw):
        add(FAIL, f"real organization {m.group(0)!r} at line {_line_of(raw, m.start())}")


def _check_numbers(doc: Doc, add: Any, ctx: Context, superseded: bool) -> None:
    allowed = ctx.allowed_pct_v1 if superseded else ctx.allowed_pct
    for s in doc.sections:
        if s.heading.startswith("Example:"):
            continue
        for rx, unit in ((PCT_RE, "percentage"), (PTS_RE, "point value")):
            for m in rx.finditer(s.text):
                if _r(float(m.group(1))) not in allowed:
                    add(FAIL, f"{unit} {m.group(0)!r} is not a fact value (section {s.heading!r})")


def _check_warnings(doc: Doc, raw: str, add: Any, ctx: Context, entry: Any, sup: bool) -> None:
    nums = ctx.fact_numbers_v1 if sup else ctx.fact_numbers
    for sec in doc.sections:
        if sec.heading.startswith("Example:"):
            continue
        for m in DOLLAR_RE.finditer(sec.text):
            if _r(float(m.group(1).replace(",", ""))) not in nums:
                add(WARN, f"dollar amount {m.group(0)!r} is not a fact value")
        for m in DAYS_RE.finditer(sec.text):
            if _r(float(m.group(1))) not in nums:
                add(WARN, f"day count {m.group(0)!r} is not a fact value")
    if not entry:
        return
    for item in entry.get("must_cover", []):
        for tok in NUM_TOKEN_RE.findall(item):
            variants = {tok, tok.replace(",", "")}
            if not any(re.search(rf"(?<![\d.]){re.escape(v)}(?!\d)", raw) for v in variants):
                add(WARN, f"must_cover number {tok!r} not found in doc (item: {item[:50]!r})")


def check_file(path: Path, ctx: Context, entry: dict[str, Any] | None = None) -> DocReport:
    stem = path.stem
    report = DocReport(doc_id=stem)

    def add(level: str, msg: str) -> None:
        report.findings.append(Finding(level, stem, msg))

    data = path.read_bytes()
    raw = data.decode("utf-8", errors="replace")
    if entry is None:
        entry = ctx.by_id.get(stem)
    try:
        doc = parse_text(raw, path)
    except Exception as exc:  # malformed TOML front matter etc.
        add(FAIL, f"cannot parse document: {type(exc).__name__}: {exc}")
        return report
    report.sections = len(doc.sections)
    report.words = sum(s.word_count for s in doc.sections)
    superseded = doc.front_matter.get("status") == "superseded"
    _check_front_matter(doc, stem, ctx, add, entry)
    _check_supersession(doc, ctx, add)
    _check_structure(doc, add)
    _check_text_rules(raw, add, ctx)
    _check_numbers(doc, add, ctx, superseded)
    _check_warnings(doc, raw, add, ctx, entry, superseded)
    return report


def check_corpus(
    docs_dir: Path, ctx: Context, allow_missing: bool = False
) -> tuple[list[DocReport], list[Finding]]:
    files = sorted(Path(docs_dir).glob("*.md"), key=lambda p: p.stem)
    reports = [check_file(p, ctx) for p in files]
    corpus: list[Finding] = []
    stems = {p.stem for p in files}
    if not allow_missing:
        for doc_id in sorted(ctx.ids - stems):
            corpus.append(Finding(FAIL, doc_id, "manifest entry has no file"))
    for stem in sorted(stems - ctx.ids):
        corpus.append(Finding(FAIL, stem, "file has no manifest entry"))
    seen: dict[str, str] = {}
    for p in files:
        try:
            doc = parse_text(p.read_text(encoding="utf-8", errors="replace"), p)
        except Exception:
            continue
        for s in doc.sections:
            if s.chunk_id in seen:
                corpus.append(Finding(FAIL, p.stem, f"chunk_id {s.chunk_id!r} is repeated"))
            seen[s.chunk_id] = p.stem
    return reports, corpus


def write_chunks_csv(docs_dir: Path, ctx: Context, out: Path) -> int:
    from rag_agent.corpus import iter_chunks

    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["chunk_id", "doc_id", "status", "heading", "words"])
        for s in iter_chunks(docs_dir):
            status = ctx.by_id.get(s.doc_id, {}).get("status", "")
            w.writerow([s.chunk_id, s.doc_id, status, s.heading, s.word_count])
            n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docs-dir", default=str(REPO_ROOT / "corpus" / "docs"))
    ap.add_argument("--facts", default=str(REPO_ROOT / "corpus" / "facts.toml"))
    ap.add_argument("--manifest", default=str(REPO_ROOT / "corpus" / "manifest.toml"))
    ap.add_argument("--allow-missing", action="store_true", help="manifest docs may lack a file")
    ap.add_argument("--list-chunks", action="store_true", help="write reports/corpus/chunks.csv")
    ap.add_argument("--chunks-out", default=str(REPO_ROOT / "reports" / "corpus" / "chunks.csv"))
    args = ap.parse_args(argv)

    ctx = build_context(load_facts(args.facts), load_manifest(args.manifest))
    docs_dir = Path(args.docs_dir)
    reports, corpus_findings = check_corpus(docs_dir, ctx, args.allow_missing)

    for r in reports:
        print(f"{'FAIL' if r.failed else 'PASS'} {r.doc_id} sections={r.sections} words={r.words}")
    findings = [f for r in reports for f in r.findings] + corpus_findings
    for level in (FAIL, WARN):
        for f in findings:
            if f.level == level:
                print(f"{level} {f.doc_id}: {f.msg}")
    n_fail = sum(f.level == FAIL for f in findings)
    n_warn = sum(f.level == WARN for f in findings)
    print(f"generated {len(reports)}/{len(ctx.ids)}, FAIL {n_fail}, WARN {n_warn}")

    if args.list_chunks:
        n = write_chunks_csv(docs_dir, ctx, Path(args.chunks_out))
        print(f"wrote {n} chunks to {args.chunks_out}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
