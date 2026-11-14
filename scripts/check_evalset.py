"""Check eval/retrieval/questions.toml against the corpus files. No network, no database.

Default: per-question rules plus count rules for every split that has questions.
--allow-incomplete: per-question rules only. --final: also reviewed, both splits complete, coverage.
Exit 1 on any FAIL.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import check_corpus
import retrieval_smoke
from rag_agent import evalset
from rag_agent.corpus import load_facts, load_manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = REPO_ROOT / "eval" / "retrieval" / "questions.toml"


def run_check(
    questions_path: Path,
    docs_dir: Path,
    manifest_path: Path,
    level: str,
    *,
    facts_path: Path | None = None,
) -> evalset.Result:
    manifest = load_manifest(manifest_path)
    ctx = check_corpus.build_context(
        load_facts(facts_path or REPO_ROOT / "corpus" / "facts.toml"), manifest
    )
    rules = evalset.Rules(
        pii=dict(check_corpus.PII_PATTERNS),
        banned=list(ctx.banned),
        smoke={qid: q for qid, q, _ in retrieval_smoke.SMOKE_QUERIES},
    )
    return evalset.check(
        evalset.load(questions_path), evalset.build_truth(docs_dir), manifest, rules, level
    )


def print_result(result: evalset.Result) -> None:
    for f in result.findings:
        print(f"{f.level} {f.qid}: {f.msg}")
    print(result.summary())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    ap.add_argument("--docs-dir", default=str(REPO_ROOT / "corpus" / "docs"))
    ap.add_argument("--manifest", default=str(REPO_ROOT / "corpus" / "manifest.toml"))
    ap.add_argument("--allow-incomplete", action="store_true")
    ap.add_argument("--final", action="store_true")
    args = ap.parse_args(argv)
    if args.allow_incomplete and args.final:
        ap.error("--allow-incomplete and --final are mutually exclusive")
    level = (
        evalset.LEVEL_FINAL
        if args.final
        else evalset.LEVEL_PER_QUESTION
        if args.allow_incomplete
        else evalset.LEVEL_DEFAULT
    )
    result = run_check(Path(args.questions), Path(args.docs_dir), Path(args.manifest), level)
    print_result(result)
    return 1 if result.n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
