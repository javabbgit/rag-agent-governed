"""Unit tests for scripts/check_corpus.py (no network, tmp dirs only)."""

import re
from pathlib import Path

import pytest

import check_corpus as cc
import smoke
from rag_agent.corpus import load_facts, load_manifest

REPO = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures" / "corpus" / "docs"
CTX = cc.build_context(
    load_facts(REPO / "corpus" / "facts.toml"), load_manifest(REPO / "corpus" / "manifest.toml")
)
GOOD = (FIX / "POL-001.md").read_text()
GOOD_V1 = (FIX / "POL-003-V1.md").read_text()
SECOND = "Each step is recorded in the file"  # sentence that opens many lines; used as anchor


def run(tmp_path, text, name="POL-001"):
    p = tmp_path / f"{name}.md"
    p.write_text(text, encoding="utf-8")
    return cc.check_file(p, CTX)


def fails(report):
    return [f.msg for f in report.findings if f.level == cc.FAIL]


def add_line(text, line):
    """Append a sentence to the first H2 section (keeps structure intact)."""
    return text.replace("## Scope of Review\n\n", f"## Scope of Review\n\n{line} ", 1)


def probe_numbers():
    m = re.search(r"\d{3} \d{3} \d{3}", smoke.PII_PROBE_TEXT)
    assert m
    digits = m.group(0).split()
    return {"spaces": " ".join(digits), "dashes": "-".join(digits), "none": "".join(digits)}


def test_good_docs_pass(tmp_path):
    assert fails(run(tmp_path, GOOD)) == []
    assert fails(run(tmp_path, GOOD_V1, "POL-003-V1")) == []


def test_loan_number_allowed(tmp_path):
    assert fails(run(tmp_path, add_line(GOOD, "See loan DMC-2026-00123 for the sample."))) == []


def test_malformed_loan_number_fails(tmp_path):
    assert any("loan" in m for m in fails(run(tmp_path, add_line(GOOD, "See DMC-26-12."))))


@pytest.mark.parametrize("kind", ["spaces", "dashes", "none"])
def test_nine_digit_variants_fail(tmp_path, kind):
    number = probe_numbers()[kind]
    out = fails(run(tmp_path, add_line(GOOD, f"Applicant {number} applies.")))
    assert any("PII" in m for m in out)


def test_pii_patterns_fail(tmp_path):
    cases = {
        "email": "Write to someone@example.invalid today.",
        "phone": "Call (" + "555" + ") " + "555" + "-" + "0100" + " today.",
        "postal": "Postal " + "K1A" + " " + "0B1" + " region.",
        "street": "At 12 Main Street today.",
        "url": "See https://example.invalid for more.",
        "www": "See www.example.invalid for more.",
        "long": "Ref " + "1234567890" + " noted.",
    }
    for name, line in cases.items():
        out = fails(run(tmp_path, add_line(GOOD, line)))
        assert any("PII" in m for m in out), name


def test_front_matter_rules(tmp_path):
    no_owner = GOOD.replace('owner = "Credit Risk"\n', "")
    assert any("lacks" in m and "owner" in m for m in fails(run(tmp_path, no_owner)))
    bad_type = GOOD.replace('doc_type = "policy"', 'doc_type = "memo"')
    assert any("doc_type" in m for m in fails(run(tmp_path, bad_type)))
    bad_aud = GOOD.replace('audience = ["staff"]', 'audience = ["public"]')
    assert any("audience" in m for m in fails(run(tmp_path, bad_aud)))
    wrong_id = GOOD.replace('id = "POL-001"', 'id = "POL-002"')
    assert any("file stem" in m for m in fails(run(tmp_path, wrong_id)))
    other_title = GOOD.replace('title = "Credit Policy Overview"', 'title = "Other"').replace(
        "# Credit Policy Overview", "# Other"
    )
    assert any("manifest" in m for m in fails(run(tmp_path, other_title)))


def test_supersession_rules(tmp_path):
    no_by = GOOD_V1.replace('superseded_by = "POL-003"\n', "")
    assert any("superseded_by" in m for m in fails(run(tmp_path, no_by, "POL-003-V1")))
    bad_sentence = GOOD_V1.replace("Superseded on 2026-09-01 by POL-003 version 2.0.", "Old.")
    assert any("Superseded on" in m for m in fails(run(tmp_path, bad_sentence, "POL-003-V1")))
    missing = GOOD_V1.replace('superseded_by = "POL-003"', 'superseded_by = "POL-999"')
    assert any("missing id" in m for m in fails(run(tmp_path, missing, "POL-003-V1")))


def test_structure_rules(tmp_path):
    two_h1 = GOOD.replace("## Roles and Ownership", "# Another\n\n## Roles and Ownership")
    assert any("H1" in m for m in fails(run(tmp_path, two_h1)))
    long_summary = GOOD.replace("It sets out the scope", "word " * 60 + "It sets out the scope")
    assert any("summary" in m for m in fails(run(tmp_path, long_summary)))
    one_h2 = GOOD[: GOOD.index("## Roles and Ownership")]
    assert any("H2 sections" in m for m in fails(run(tmp_path, one_h2)))
    short = GOOD.replace(
        "## Record Keeping Rules\n\n", "## Record Keeping Rules\n\nShort.\n\n## Extra Part\n\n"
    )
    assert any("words" in m for m in fails(run(tmp_path, short)))
    dup = GOOD.replace("## Roles and Ownership", "## Scope of review")
    assert any("duplicate" in m for m in fails(run(tmp_path, dup)))
    generic = GOOD.replace("## Roles and Ownership", "## Overview")
    assert any("generic" in m for m in fails(run(tmp_path, generic)))


def test_non_ascii_and_curly_quote_fail(tmp_path):
    assert any("non-ASCII" in m for m in fails(run(tmp_path, add_line(GOOD, f"It{chr(0x2019)}s."))))


def test_percentages_against_real_facts(tmp_path):
    ok = fails(run(tmp_path, add_line(GOOD, "Rates of 4.15% and 6.44% are quoted.")))
    assert ok == []
    bad = fails(run(tmp_path, add_line(GOOD, "A rate of 4.16% is quoted.")))
    assert any("4.16" in m for m in bad)
    pts = fails(run(tmp_path, add_line(GOOD, "A buffer of 1.75 pts and 3.3 points.")))
    assert any("3.3" in m for m in pts) and not any("1.75" in m for m in pts)


def test_example_sections_skip_percentages(tmp_path):
    text = GOOD.replace("## Roles and Ownership", "## Example: Sample Case")
    text = text.replace("The scope covers", "A rate of 4.16% is shown. The scope covers")
    text = text.replace("Each department names", "A rate of 4.16% is shown. Each department names")
    out = fails(run(tmp_path, text))
    assert any("4.16" in m for m in out)  # still fails in the non-example section
    only_example = GOOD.replace("## Roles and Ownership", "## Example: Sample Case").replace(
        "Each department names", "A rate of 4.16% is shown. Each department names"
    )
    assert fails(run(tmp_path, only_example)) == []


def test_v1_values_only_in_superseded(tmp_path):
    assert fails(run(tmp_path, GOOD_V1, "POL-003-V1")) == []  # 33% and 40%
    assert any("33" in m for m in fails(run(tmp_path, add_line(GOOD, "Limit 33% applies."))))


@pytest.mark.parametrize("word", ["Construction", "portability", "Password", "Crypto"])
def test_banned_terms_fail(tmp_path, word):
    out = fails(run(tmp_path, add_line(GOOD, f"The {word} topic is mentioned.")))
    assert any("banned" in m for m in out)


def test_banned_term_word_start_only(tmp_path):
    assert fails(run(tmp_path, add_line(GOOD, "The file was reconstructed later."))) == []


@pytest.mark.parametrize("org", ["OSFI", "cmhc", "B-20", "Canada Guaranty", "TD Bank", "RBC"])
def test_real_orgs_fail(tmp_path, org):
    out = fails(run(tmp_path, add_line(GOOD, f"Guidance from {org} applies.")))
    assert any("real organization" in m for m in out)


def test_org_whole_word_only(tmp_path):
    assert fails(run(tmp_path, add_line(GOOD, "The tdbankless rbcs are fine."))) == []


def test_warnings_do_not_fail(tmp_path):
    r = run(tmp_path, add_line(GOOD, "A fee of $777 and 77 days are mentioned."))
    assert fails(r) == []
    warns = [f.msg for f in r.findings if f.level == cc.WARN]
    assert any("$777" in w for w in warns) and any("77 days" in w for w in warns)


def test_corpus_level_checks(tmp_path):
    (tmp_path / "POL-001.md").write_text(GOOD)
    (tmp_path / "ZZZ-999.md").write_text(GOOD.replace('id = "POL-001"', 'id = "ZZZ-999"'))
    _, corpus = cc.check_corpus(tmp_path, CTX, allow_missing=True)
    assert [f.doc_id for f in corpus] == ["ZZZ-999"]
    _, corpus = cc.check_corpus(tmp_path, CTX, allow_missing=False)
    assert any("no file" in f.msg for f in corpus)


def test_main_exit_codes_and_chunks_csv(tmp_path, capsys):
    out = tmp_path / "chunks.csv"
    argv = ["--docs-dir", str(FIX), "--allow-missing", "--list-chunks", "--chunks-out", str(out)]
    assert cc.main(argv) == 0
    text = capsys.readouterr().out
    assert "generated 2/40, FAIL 0" in text
    lines = out.read_text().splitlines()
    assert lines[0] == "chunk_id,doc_id,status,heading,words"
    assert lines[1].startswith("POL-001#summary,POL-001,current,summary,")
    bad = tmp_path / "docs"
    bad.mkdir()
    (bad / "POL-001.md").write_text(add_line(GOOD, "A rate of 4.16% is quoted."))
    assert cc.main(["--docs-dir", str(bad), "--allow-missing"]) == 1


def test_warnings_skip_example_sections(tmp_path):
    line = "A fee of $777 and 77 days are mentioned."
    in_example = GOOD.replace("## Roles and Ownership", "## Example: Sample Case").replace(
        "Each department names", f"{line} Each department names"
    )
    r = run(tmp_path, in_example)
    assert fails(r) == []
    warns = [f.msg for f in r.findings if f.level == cc.WARN]
    assert not any("777" in w or "77 days" in w for w in warns)
    outside = run(tmp_path, add_line(GOOD, line))
    assert any("$777" in f.msg for f in outside.findings if f.level == cc.WARN)
