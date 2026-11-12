import copy
import re
from pathlib import Path

import pytest

import check_corpus
import retrieval_smoke
from rag_agent import evalset
from rag_agent.corpus import load_manifest

FIX = Path(__file__).parent / "fixtures" / "evalset"
REPO = Path(__file__).resolve().parent.parent
TRUTH = evalset.build_truth(FIX / "docs")
MANIFEST = load_manifest(REPO / "corpus" / "manifest.toml")
RULES = evalset.Rules(
    pii=dict(check_corpus.PII_PATTERNS),
    banned=[re.compile(r"\bcrypto", re.IGNORECASE), re.compile(r"\bcar loan", re.IGNORECASE)],
    smoke={qid: q for qid, q, _ in retrieval_smoke.SMOKE_QUERIES},
)

BASE = {
    "id": "D13",
    "split": "dev",
    "kind": "answerable",
    "category": "exact",
    "persona": "staff",
    "query": "What is the largest share of income allowed for all debts?",
    "answer": "42 percent",
    "gold": ["POL-003#ratio-limits"],
    "related": [],
    "nonrel": [],
    "origin": "new",
    "reviewed": True,
}
OOS = {
    "id": "D31",
    "split": "dev",
    "kind": "oos",
    "category": "oos_far",
    "persona": "borrower",
    "query": "Can I get a loan to buy a used vehicle?",
    "answer": "",
    "gold": [],
    "related": [],
    "nonrel": [],
    "origin": "new",
    "reviewed": True,
    "topic": "car loans",
}


def mk(base=BASE, **kw):
    q = copy.deepcopy(base)
    q.update(kw)
    return q


def run(qs, level=evalset.LEVEL_PER_QUESTION):
    return evalset.check({"version": 1, "q": qs}, TRUTH, MANIFEST, RULES, level)


def fails(result, needle=""):
    return [f for f in result.findings if f.level == evalset.FAIL and needle in f.msg]


def test_fixture_file_passes():
    data = evalset.load(FIX / "questions_ok.toml")
    r = evalset.check(data, TRUTH, MANIFEST, RULES, evalset.LEVEL_PER_QUESTION)
    assert r.findings == [], r.findings
    assert r.summary() == "evalset: dev 1+1, test 0+0, FAIL 0, WARN 0"


def test_base_questions_pass():
    assert run([BASE, OOS]).findings == []


@pytest.mark.parametrize(
    "kw,needle",
    [
        ({"extra": 1}, "extra field"),
        ({"persona": "robot"}, "persona"),
        ({"reviewed": "yes"}, "wrong type"),
        ({"category": "oos_far"}, "category"),
        ({"id": "T13"}, "id does not match"),
        ({"id": "D31"}, "outside"),
        ({"query": "What is the maximum TDS ratio allowed in Zürich?"}, "non-ASCII"),
        ({"query": "What is the largest share of income allowed for debts"}, "'?'"),
        ({"query": "Max TDS ratio?"}, "words"),
        (
            {"query": "What is the largest share of income allowed for POL-003 debts?"},
            "document id",
        ),
        ({"query": "What is pol-003 about for the largest income share?"}, "document id"),
        ({"query": "What happened to loan DMC-2026-00012 in the file system?"}, "loan number"),
        ({"query": "Is 193 456 787 the largest share of income allowed?"}, "PII"),
        ({"query": "Is the car loan share of income allowed to be 42 percent?"}, "banned term"),
        ({"gold": []}, "gold is empty"),
        ({"gold": ["POL-003#nope"]}, "does not exist"),
        ({"related": ["POL-003#nope"]}, "does not exist"),
        ({"nonrel": ["POL-003#nope"]}, "does not exist"),
        ({"gold": ["POL-003-V1#former-ratio-limits"], "answer": "40 percent"}, "superseded chunk"),
        ({"related": ["POL-003-V1#former-ratio-limits"]}, "superseded chunk"),
        ({"related": ["POL-003#ratio-limits"]}, "overlap"),
        ({"answer": ""}, "answer is empty"),
        ({"answer": "99 percent"}, "not found in any gold chunk"),
        (
            {"query": "Is the cap of 42 percent the largest share of income allowed?"},
            "in the query",
        ),
    ],
)
def test_per_question_fail_rules(kw, needle):
    assert fails(run([mk(**kw)]), needle), run([mk(**kw)]).findings


def test_answer_whitespace_and_case_insensitive():
    assert not fails(run([mk(answer="MAXIMUM  TDS ratio\nis 42 percent")]))


def test_duplicate_id_and_duplicate_query():
    assert fails(run([BASE, BASE]), "duplicate id")
    q2 = mk(id="D14", query="what is the  LARGEST share of income allowed for all debts?")
    assert fails(run([BASE, q2]), "duplicate of D13")


def test_duplicate_query_across_splits():
    t = mk(id="T01", split="test", query=BASE["query"].upper())
    assert fails(run([BASE, t]), "duplicate of D13")


def test_smoke_origin_needs_exact_query():
    assert fails(run([mk(id="D01", origin="smoke-Q01")]), "smoke")
    ok = mk(id="D01", origin="smoke-Q01", query="What is the maximum TDS ratio?")
    assert not fails(run([ok]))


def test_test_query_equal_to_smoke_query_fails():
    t = mk(id="T01", split="test", query="What is the maximum TDS ratio?")
    assert fails(run([t]), "equals a smoke query")


def test_query_with_loan_number_and_doc_id_named_in_spec():
    assert fails(
        run([mk(query="Does POL-003 say what the largest share of income is?")]), "POL-003"
    )


def test_jaccard_warning_between_test_and_dev():
    t = mk(id="T01", split="test", query="What is the largest share of income allowed for debts?")
    r = run([BASE, t])
    assert [f for f in r.findings if f.level == evalset.WARN and f.qid == "T01"]


def test_order_rule():
    t = mk(id="T01", split="test", query="Where do the largest income shares come from today?")
    assert fails(run([t, BASE]), "ordered")


def test_paraphrase_sharing_heading_word_fails():
    q = mk(category="paraphrase", query="Which ratio limits apply to my debts and income?")
    assert fails(run([q]), "shares heading tokens")
    ok = mk(category="paraphrase", query="What is the largest share of income allowed for debts?")
    assert not fails(run([ok]), "paraphrase")


def test_confusable_needs_product_docs():
    assert fails(run([mk(category="confusable")]), "confusable")
    ok = mk(
        category="confusable",
        gold=["PRD-001#term"],
        answer="five years",
        query="Which closed product has the longest of the terms offered to borrowers?",
    )
    assert not fails(run([ok]), "confusable")


def test_confusable_allows_policy_or_faq_gold_alongside_product():
    ok = mk(
        category="confusable",
        gold=["PRD-001#term", "FAQ-001#early-renewal"],
        answer="five years",
        query="Which closed product has the longest of the terms offered to borrowers?",
    )
    assert not fails(run([ok]), "confusable")
    ok2 = mk(ok, gold=["PRD-001#term", "POL-003#ratio-limits"])
    assert not fails(run([ok2]), "confusable")


def test_superseded_trap_answer_in_v1_fails_and_otherwise_passes():
    bad = mk(category="superseded_trap", gold=["POL-003#ratio-limits"], answer="gross income")
    assert fails(run([bad]), "also found in POL-003-V1")
    ok = mk(category="superseded_trap", gold=["POL-003#ratio-limits"], answer="42 percent")
    assert not fails(run([ok]), "superseded_trap")
    outside = mk(category="superseded_trap", gold=["PRD-001#term"], answer="five years")
    assert fails(run([outside]), "no gold doc in")
    for extra in ("FAQ-001#early-renewal", "PRD-001#term"):
        mixed = mk(
            category="superseded_trap",
            gold=["POL-003#ratio-limits", extra],
            answer="42 percent",
        )
        assert not fails(run([mixed]), "superseded_trap")
    mixed_bad = mk(
        category="superseded_trap",
        gold=["POL-003#ratio-limits", "FAQ-001#early-renewal"],
        answer="gross income",
    )
    assert fails(run([mixed_bad]), "also found in POL-003-V1")


def test_fine_grained():
    ok = mk(category="fine_grained", gold=["POL-009#lump-sum-limit"], answer="15 percent of the")
    assert not fails(run([ok]), "fine_grained")
    few = mk(category="fine_grained")
    assert fails(run([few]), "fewer than 5 H2")
    two = mk(
        category="fine_grained",
        gold=["POL-009#lump-sum-limit", "POL-009#payment-increase-limit"],
        answer="15 percent",
    )
    assert fails(run([two]), "exactly one")


def test_table_answer_must_be_in_table_line():
    ok = mk(category="table", gold=["POL-003#limits-table"], answer="| TDS | 42 percent |")
    assert not fails(run([ok]), "table")
    bad = mk(category="table", gold=["POL-003#ratio-limits"], answer="42 percent")
    assert fails(run([bad]), "table line")


def test_multi_doc():
    one = mk(category="multi_doc")
    assert fails(run([one]), "fewer than 2 docs")
    two = mk(
        category="multi_doc",
        gold=["POL-003#ratio-limits", "POL-009#lump-sum-limit"],
        answer="42 percent",
    )
    assert not fails(run([two]), "multi_doc")


@pytest.mark.parametrize(
    "kw,needle",
    [
        ({"gold": ["POL-003#ratio-limits"]}, "gold and related must be empty"),
        ({"related": ["POL-003#ratio-limits"]}, "gold and related must be empty"),
        ({"answer": "x"}, "answer must be empty"),
        ({"topic": "student loans"}, None),
        ({"topic": "reverse mortgages"}, "topic"),
        ({"category": "oos_near"}, "topic"),
        ({"topic": 3}, "wrong type"),
    ],
)
def test_oos_rules(kw, needle):
    r = run([mk(OOS, **kw)])
    if needle is None:
        assert not fails(r)
    else:
        assert fails(r, needle), r.findings


def test_oos_topic_case_insensitive():
    assert not fails(run([mk(OOS, topic="CAR LOANS")]))


# --- count rules -------------------------------------------------------------------------------

DEV_FAR = ["car loans", "travel insurance", "cryptocurrency", "IT password resets"]
DEV_NEAR = [
    "reverse mortgages",
    "blend-and-extend",
    "cottage or recreational properties",
    "co-signers and guarantors",
]
CATS = list(evalset.ANSWERABLE_CATEGORIES)


def synth(split, n_ans, cats, n_far, n_near, far_topics, near_topics, personas=None):
    p, offset = ("D", 30) if split == "dev" else ("T", 50)
    qs = []
    for i in range(n_ans):
        qs.append(
            mk(
                id=f"{p}{i + 1:02d}",
                split=split,
                category=cats[i],
                persona=(personas or ["staff"])[i % len(personas or ["staff"])],
                query=f"synthetic question number {i} about something?",
                origin=f"smoke-Q{i + 1:02d}" if split == "dev" and i < 12 else "new",
            )
        )
    for j in range(n_far):
        qs.append(
            mk(
                OOS,
                id=f"{p}{offset + 1 + j:02d}",
                split=split,
                topic=far_topics[j],
                category="oos_far",
            )
        )
    for j in range(n_near):
        qs.append(
            mk(
                OOS,
                id=f"{p}{offset + 1 + n_far + j:02d}",
                split=split,
                topic=near_topics[j],
                category="oos_near",
            )
        )
    return qs


def dev_cats(n=30):
    return [CATS[i % 7] for i in range(n)]


def count_fails(qs, level=evalset.LEVEL_DEFAULT):
    r = run(qs, level)
    return [
        f.msg
        for f in r.findings
        if f.level == evalset.FAIL and f.msg.startswith(("count", "topics"))
    ]


def good_dev():
    return synth("dev", 30, dev_cats(), 4, 4, DEV_FAR, DEV_NEAR)


def test_dev_counts_pass():
    assert count_fails(good_dev()) == []


def test_dev_counts_not_checked_when_allow_incomplete():
    assert count_fails(good_dev()[:5], evalset.LEVEL_PER_QUESTION) == []


def test_dev_wrong_total():
    assert any("need 30" in m for m in count_fails(good_dev()[1:]))


def test_dev_category_minimum():
    qs = synth("dev", 30, ["exact"] * 28 + ["table", "table"], 4, 4, DEV_FAR, DEV_NEAR)
    assert any("category paraphrase" in m for m in count_fails(qs))


def test_dev_smoke_prefix_required():
    qs = good_dev()
    qs[3]["origin"] = "new"
    assert any("D04 must have origin" in m for m in count_fails(qs))


def test_dev_oos_split_and_topics():
    qs = synth("dev", 30, dev_cats(), 5, 3, DEV_FAR + ["student loans"], DEV_NEAR)
    assert any("need 4 + 4" in m for m in count_fails(qs))
    qs = synth("dev", 30, dev_cats(), 4, 4, ["student loans"] + DEV_FAR[1:], DEV_NEAR)
    assert any("topics" in m for m in count_fails(qs))


TEST_FAR = [
    "student loans",
    "RRSP or investment advice",
    "life insurance claims",
    "property tax appeals",
    "employee vacation policy",
    "commercial real estate",
]
TEST_NEAR = [
    "bridge financing",
    "mortgage portability",
    "construction draw mortgages",
    "foreign buyer rules",
    "payment deferral or skip-a-payment",
    "mortgage assumption by a buyer",
]


def quota_cats():
    out = []
    for c, n in evalset.TEST_QUOTAS.items():
        out += [c] * n
    return out


def good_test(cats=None, personas=("borrower", "broker", "staff")):
    return synth("test", 50, cats or quota_cats(), 6, 6, TEST_FAR, TEST_NEAR, list(personas))


def test_test_counts_pass():
    assert count_fails(good_test()) == []


def test_test_quota_wrong():
    cats = quota_cats()
    cats[0] = "table"
    assert any("category exact has 7" in m for m in count_fails(good_test(cats)))


def test_test_persona_minimum():
    assert any(
        "persona broker" in m for m in count_fails(good_test(personas=("staff", "borrower")))
    )


def test_test_topics_must_be_the_other_twelve():
    qs = synth("test", 50, quota_cats(), 6, 6, DEV_FAR + TEST_FAR[:2], TEST_NEAR)
    assert any("topics" in m for m in count_fails(qs))


def test_topic_used_twice_fails():
    qs = synth("test", 50, quota_cats(), 6, 6, [TEST_FAR[0]] * 6, TEST_NEAR)
    assert any("topics" in m for m in count_fails(qs))


# --- final -------------------------------------------------------------------------------------


def test_final_requires_reviewed_true():
    qs = good_dev()
    qs[0]["reviewed"] = False
    r = run(qs, evalset.LEVEL_FINAL)
    assert [f for f in r.findings if f.qid == "D01" and "reviewed" in f.msg]


def test_final_requires_both_splits_complete():
    r = run(good_dev(), evalset.LEVEL_FINAL)
    assert any(f.qid == "test" and f.msg.startswith("count") for f in r.findings)


def test_final_coverage_fail_and_warn():
    r = run([BASE], evalset.LEVEL_FINAL)
    uncovered = {f.qid for f in r.findings if f.msg.startswith("coverage")}
    assert uncovered == {"FAQ-001", "POL-009", "PRD-001"}
    many = [
        mk(id=f"D{i:02d}", query=f"another synthetic question number {i} here?")
        for i in range(1, 9)
    ]
    r = run(many, evalset.LEVEL_FINAL)
    assert [f for f in r.findings if f.level == evalset.WARN and f.qid == "POL-003"]


def test_summary_line():
    r = run([BASE, OOS])
    assert r.summary() == "evalset: dev 1+1, test 0+0, FAIL 0, WARN 0"
