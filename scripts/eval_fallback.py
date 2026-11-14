"""Offline fallback-threshold evaluator (make eval-fallback). Sends no request.

Reads one retrieval report JSON (reports/eval/retrieval_<split>_<UTC>.json from a run that
included mode rerank). Signals: rerank_top1 (required) and vector_top1 (comparison only).
Fallback means top-1 score < t; "correct" = share of out-of-scope questions that fall back,
"false" = share of answerable questions that fall back.

dev:  AUROC per signal; the Youden threshold for rerank_top1 and the rates at it.
      --write-threshold freezes it into eval/fallback/threshold.toml (once; never overwritten).
test: applies the committed threshold only (clean tree, threshold committed at the report's
      commit, same rerank_n), appends one line to eval/fallback/test_runs.csv (never rewritten).
Reports hold ids, scores and counts only: never chunk text.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rag_agent import evalset, metrics

REPO_ROOT = Path(__file__).resolve().parent.parent
THRESHOLD_PATH = REPO_ROOT / "eval" / "fallback" / "threshold.toml"
THRESHOLD_REL = "eval/fallback/threshold.toml"
TEST_RUNS_PATH = REPO_ROOT / "eval" / "fallback" / "test_runs.csv"
RULE = "youden-midpoint-ties-high"
SIGNAL = "rerank_top1"
LEDGER_HEADER = [
    "utc",
    "report_file",
    "report_fingerprint",
    "questions_sha256",
    "threshold",
    "n_oos",
    "n_oos_fallback",
    "n_answerable",
    "n_answerable_fallback",
    "correct_rate",
    "false_rate",
]


def _f(x: float) -> str:
    return f"{x:.4f}"


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Refused(Exception):
    """A precondition failed; exit 2 and write nothing."""


def committed_threshold(commit: str) -> str | None:
    """Content of eval/fallback/threshold.toml at the given commit, or None if absent."""
    try:
        out = subprocess.run(
            ["git", "show", f"{commit}:{THRESHOLD_REL}"],
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8")


def load_items(report: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    items = []
    for qid, q in sorted(report["questions"].items()):
        m = q["modes"].get(mode)
        if m is None or m.get("top1_score") is None:
            raise Refused(f"question {qid} has no top-1 score for mode {mode}")
        items.append(
            {
                "id": qid,
                "kind": q["kind"],
                "category": q["category"],
                "score": float(m["top1_score"]),
                "has_gold": m.get("first_gold_rank") is not None,
            }
        )
    return items


def split_scores(items: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    pos = [i["score"] for i in items if i["kind"] == "answerable"]
    neg = [i["score"] for i in items if i["kind"] == "oos"]
    if not pos or not neg:
        raise Refused("the report needs answerable and out-of-scope questions")
    return pos, neg


def _rate(flags: list[int]) -> dict[str, Any]:
    n = len(flags)
    out: dict[str, Any] = {"num": sum(flags), "den": n, "rate": sum(flags) / n}
    out["ci"] = list(metrics.bootstrap_ci([float(x) for x in flags]))
    return out


def rates_at(items: list[dict[str, Any]], t: float) -> dict[str, Any]:
    """Rates with numerators/denominators, by oos and answerable category, plus missed gold."""
    oos = [i for i in items if i["kind"] == "oos"]
    ans = [i for i in items if i["kind"] == "answerable"]
    out: dict[str, Any] = {
        "correct_fallback": _rate([int(i["score"] < t) for i in oos]),
        "false_fallback": _rate([int(i["score"] < t) for i in ans]),
        "correct_by_category": {},
        "false_by_category": {},
    }
    for cat in evalset.OOS_CATEGORIES:
        sel = [i for i in oos if i["category"] == cat]
        if sel:
            out["correct_by_category"][cat] = _rate([int(i["score"] < t) for i in sel])
    for cat in evalset.ANSWERABLE_CATEGORIES:
        sel = [i for i in ans if i["category"] == cat]
        if sel:
            out["false_by_category"][cat] = _rate([int(i["score"] < t) for i in sel])
    out["passed_without_gold"] = sum(1 for i in ans if i["score"] >= t and not i["has_gold"])
    return out


def threshold_toml(
    t: float, lo: float, hi: float, rates: dict[str, Any], report: dict[str, Any]
) -> str:
    return (
        "# Frozen fallback threshold, written once by `make eval-fallback SPLIT=dev REPORT=...\n"
        "# WRITE_THRESHOLD=1`. Never edit by hand; commit it before any test run.\n"
        f'signal = "{SIGNAL}"\n'
        f"threshold = {t!r}\n"
        f'rule = "{RULE}"\n'
        f"interval_lo = {lo!r}\n"
        f"interval_hi = {hi!r}\n"
        f"rerank_n = {int(report['rerank_n'])}\n"
        f'reranker_model_sha = "{report["reranker"]["model_sha"]}"\n'
        f'dev_questions_sha256 = "{report["questions_sha256"]}"\n'
        f'dev_report_fingerprint = "{report["fingerprint"]}"\n'
        f"dev_correct_fallback_rate = {rates['correct_fallback']['rate']!r}\n"
        f"dev_false_fallback_rate = {rates['false_fallback']['rate']!r}\n"
    )


def threshold_is_set(path: Path) -> bool:
    if not path.exists():
        return False
    return "threshold" in tomllib.loads(path.read_text(encoding="utf-8"))


def append_ledger(path: Path, row: list[Any]) -> None:
    new = not path.exists() or path.stat().st_size == 0
    prefix = ""
    if not new and not path.read_text(encoding="utf-8").endswith("\n"):
        prefix = "\n"
    with open(path, "a", encoding="utf-8", newline="") as fh:
        if prefix:
            fh.write(prefix)
        w = csv.writer(fh, lineterminator="\n")
        if new:
            w.writerow(LEDGER_HEADER)
        w.writerow(row)


def rates_lines(label: str, rates: dict[str, Any]) -> list[str]:
    c, f = rates["correct_fallback"], rates["false_fallback"]
    out = [
        f"- {label}: correct fallback {c['num']}/{c['den']} = {_f(c['rate'])} "
        f"[{_f(c['ci'][0])}, {_f(c['ci'][1])}]; false fallback {f['num']}/{f['den']} = "
        f"{_f(f['rate'])} [{_f(f['ci'][0])}, {_f(f['ci'][1])}]"
    ]
    for cat, r in rates["correct_by_category"].items():
        out.append(f"  - {cat}: {r['num']}/{r['den']} fall back (correct)")
    for cat, r in rates["false_by_category"].items():
        out.append(f"  - {cat}: {r['num']}/{r['den']} fall back (false)")
    out.append(f"  - answerable passed without gold in top 10: {rates['passed_without_gold']}")
    return out


def markdown(res: dict[str, Any]) -> str:
    out = [
        f"# Fallback threshold: {res['split']}",
        "",
        f"report {res['report_file']}, fingerprint {res['report_fingerprint']}, "
        f"questions_sha256 {res['questions_sha256']}, rerank_n {res['rerank_n']}",
        "",
        "## AUROC",
        "",
    ]
    out += [f"- {k}: {_f(v)}" for k, v in res["auroc"].items()]
    out += ["", "## Threshold", "", f"- {res['threshold_source']}: t = {res['threshold']!r}"]
    if "interval" in res:
        lo, hi = res["interval"]
        out.append(f"- interval ({lo!r}, {hi!r}], J = {_f(res['j'])}")
    out += rates_lines("rates at t", res["rates"])
    if res.get("reference"):
        ref = res["reference"]
        out += [
            "",
            f"## Test-optimal Youden t = {ref['t']:.4f} (reference only, not used)",
            "",
            f"- interval ({ref['lo']:.4f}, {ref['hi']:.4f}], J = {_f(ref['j'])}",
            *rates_lines("rates at reference t", ref["rates"]),
        ]
    return "\n".join(out) + "\n"


def run_fallback(
    split: str,
    report_path: Path,
    *,
    write_threshold: bool = False,
    threshold_path: Path | None = None,
    test_runs_path: Path | None = None,
    reports_dir: Path | None = None,
) -> int:
    threshold_path = threshold_path or THRESHOLD_PATH
    test_runs_path = test_runs_path or TEST_RUNS_PATH
    reports_dir = reports_dir or REPO_ROOT / "reports" / "eval"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("split") != split:
            raise Refused(f"report split {report.get('split')!r} is not {split!r}")
        if "rerank" not in report.get("modes", []) or not report.get("rerank_n"):
            raise Refused("the report must contain mode rerank")
        items = load_items(report, "rerank")
        pos, neg = split_scores(items)
        signals = {SIGNAL: (pos, neg)}
        if "vector" in report["modes"]:
            signals["vector_top1"] = split_scores(load_items(report, "vector"))
        res: dict[str, Any] = {
            "split": split,
            "report_file": report_path.name,
            "report_fingerprint": report["fingerprint"],
            "questions_sha256": report["questions_sha256"],
            "rerank_n": report["rerank_n"],
            "reranker": report["reranker"],
            "auroc": {k: metrics.auroc(p, n) for k, (p, n) in signals.items()},
            "n_answerable": len(pos),
            "n_oos": len(neg),
        }
        if split == "dev":
            if write_threshold and threshold_is_set(threshold_path):
                raise Refused(f"{threshold_path.name} is already set; it is never overwritten")
            t, lo, hi, j = metrics.youden_threshold(pos, neg)
            res.update(
                threshold=t,
                threshold_source=f"dev Youden, rule {RULE}",
                interval=[lo, hi],
                j=j,
                rates=rates_at(items, t),
            )
        else:
            if write_threshold:
                raise Refused("--write-threshold is for the dev split only")
            if report.get("git_dirty") is not False:
                raise Refused("the report was made on a dirty tree (git_dirty is not false)")
            commit = report.get("git_commit")
            text = committed_threshold(commit) if commit else None
            if text is None:
                raise Refused(f"{THRESHOLD_REL} is not committed at the report's git_commit")
            frozen = tomllib.loads(text)
            if "threshold" not in frozen:
                raise Refused("the committed threshold.toml has no threshold set")
            if frozen.get("signal") != SIGNAL:
                raise Refused(f"threshold signal {frozen.get('signal')!r} is not {SIGNAL}")
            if int(frozen["rerank_n"]) != int(report["rerank_n"]):
                raise Refused(
                    f"report rerank_n {report['rerank_n']} differs from the threshold's "
                    f"{frozen['rerank_n']}"
                )
            t = float(frozen["threshold"])
            rt, rlo, rhi, rj = metrics.youden_threshold(pos, neg)
            res.update(
                threshold=t,
                threshold_source="committed dev threshold",
                rates=rates_at(items, t),
                reference={
                    "t": rt,
                    "lo": rlo,
                    "hi": rhi,
                    "j": rj,
                    "rates": rates_at(items, rt),
                },
            )
    except Refused as exc:
        print(f"[REFUSED] {exc}")
        return 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"[FAIL] cannot use the report: {type(exc).__name__}")
        return 2

    now = datetime.now(UTC)
    reports_dir.mkdir(parents=True, exist_ok=True)
    base = reports_dir / f"fallback_{split}_{now.strftime('%Y%m%dT%H%M%SZ')}"
    base.with_suffix(".json").write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    base.with_suffix(".md").write_text(markdown(res), encoding="utf-8")
    if split == "dev" and write_threshold:
        threshold_path.parent.mkdir(parents=True, exist_ok=True)
        threshold_path.write_text(
            threshold_toml(res["threshold"], *res["interval"], res["rates"], report),
            encoding="utf-8",
        )
        print(f"wrote {threshold_path}")
    if split == "test":
        c, f = res["rates"]["correct_fallback"], res["rates"]["false_fallback"]
        append_ledger(
            test_runs_path,
            [
                _iso(now),
                report_path.name,
                report["fingerprint"],
                report["questions_sha256"],
                repr(res["threshold"]),
                c["den"],
                c["num"],
                f["den"],
                f["num"],
                _f(c["rate"]),
                _f(f["rate"]),
            ],
        )
    for k, v in res["auroc"].items():
        print(f"AUROC {k} {_f(v)}")
    print(f"t = {res['threshold']!r} ({res['threshold_source']})")
    if "reference" in res:
        print(f"test-optimal Youden t = {res['reference']['t']:.4f} (reference only, not used)")
    print(f"wrote {base.with_suffix('.json')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", choices=evalset.SPLITS, required=True)
    ap.add_argument("--report", required=True, help="retrieval report JSON (with mode rerank)")
    ap.add_argument("--write-threshold", action="store_true")
    args = ap.parse_args(argv)
    return run_fallback(args.split, Path(args.report), write_threshold=args.write_threshold)


if __name__ == "__main__":
    sys.exit(main())
