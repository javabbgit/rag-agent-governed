import pytest

from rag_agent.bm25 import BM25Index, tokenize
from rag_agent.retrieve import BM25_B, BM25_K1


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("FX5-C lump-sum payments", "fx5-c fx5 lump-sum lump sum payment"),
        ("What is the maximum GDS ratio?", "maximum gds ratio"),
        ("form EXC-100", "form exc-100 exc 100"),
        ("rate of 4.69%", "rate 4.69"),
        ("DMC-2026-00123", "dmc-2026-00123 dmc 2026 00123"),
        ("policies and fees", "policy fee"),
    ],
)
def test_tokenize_required_cases(text, expected):
    assert tokenize(text) == expected.split()


def test_tokenize_plural_rule_edges():
    # "ies" needs length > 4, so "ties" falls to the plain "s" rule; ss/us/is are kept.
    assert tokenize("ties class bonus analysis loans") == [
        "tie",
        "class",
        "bonus",
        "analysis",
        "loan",
    ]


def test_tokenize_keeps_order_and_duplicates():
    assert tokenize("fee fees Fee rate") == ["fee", "fee", "fee", "rate"]


def test_tokenize_explicit_stopwords():
    assert tokenize("the rate", frozenset()) == ["the", "rate"]


# Toy collection, k1 = 1.2, b = 0.75, no stopwords. N = 3.
#   d1 = "alpha beta"               dl = 2
#   d2 = "alpha alpha gamma"        dl = 3
#   d3 = "gamma delta delta delta"  dl = 4          avgdl = (2 + 3 + 4) / 3 = 3
DOCS = {"d1": "alpha beta", "d2": "alpha alpha gamma", "d3": "gamma delta delta delta"}


@pytest.fixture
def index():
    return BM25Index(DOCS, 1.2, 0.75, frozenset())


def test_constants_are_the_specified_ones():
    assert (BM25_K1, BM25_B) == (1.2, 0.75)


def test_bm25_single_term(index):
    # alpha: n = 2, idf = ln(1 + (3 - 2 + 0.5) / (2 + 0.5)) = ln(1.6) = 0.4700
    # d1: tf 1, dl/avgdl = 2/3, norm = 1 - 0.75 + 0.75 * 2/3 = 0.75
    #     0.4700 * 1 * 2.2 / (1 + 1.2 * 0.75) = 0.4700 * 2.2 / 1.9 = 0.5442
    # d2: tf 2, dl/avgdl = 1, norm = 1
    #     0.4700 * 2 * 2.2 / (2 + 1.2) = 0.4700 * 4.4 / 3.2 = 0.6463
    s = index.scores("alpha")
    assert set(s) == {"d1", "d2"}
    assert round(s["d1"], 4) == 0.5442
    assert round(s["d2"], 4) == 0.6463


def test_bm25_two_terms_sum(index):
    # gamma: n = 2, idf = 0.4700 again.
    # d2 gamma: tf 1, norm 1: 0.4700 * 2.2 / (1 + 1.2) = 0.4700 -> d2 = 0.6463 + 0.4700 = 1.1163
    # d3 gamma: tf 1, dl/avgdl = 4/3, norm = 0.25 + 0.75 * 4/3 = 1.25
    #           0.4700 * 2.2 / (1 + 1.2 * 1.25) = 0.4700 * 2.2 / 2.5 = 0.4136
    # d1 has no gamma: stays 0.5442
    s = index.scores("alpha gamma")
    assert [round(s[c], 4) for c in ("d1", "d2", "d3")] == [0.5442, 1.1163, 0.4136]
    assert [c for c, _ in index.top("alpha gamma", 10)] == ["d2", "d1", "d3"]


def test_bm25_rare_term(index):
    # delta: n = 1, idf = ln(1 + (3 - 1 + 0.5) / (1 + 0.5)) = ln(2.6667) = 0.9808
    # d3: tf 3, norm 1.25: 0.9808 * 3 * 2.2 / (3 + 1.2 * 1.25) = 0.9808 * 6.6 / 4.5 = 1.4385
    s = index.scores("delta")
    assert list(s) == ["d3"] and round(s["d3"], 4) == 1.4385


def test_bm25_distinct_query_terms(index):
    assert index.scores("alpha alpha ALPHA") == index.scores("alpha")


def test_bm25_unknown_and_empty_query(index):
    assert index.top("zzz", 5) == []
    assert index.top("", 5) == []


def test_bm25_ties_by_chunk_id_and_cut():
    idx = BM25Index({"b": "word", "a": "word", "c": "word"}, 1.2, 0.75, frozenset())
    assert [c for c, _ in idx.top("word", 2)] == ["a", "b"]


def test_bm25_keep_filters_after_scoring(index):
    # idf is unchanged by the filter: d2 keeps 0.6463 although d1 is dropped.
    top = index.top("alpha", 10, keep={"d2", "d3"})
    assert [(c, round(s, 4)) for c, s in top] == [("d2", 0.6463)]
