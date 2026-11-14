import pytest

from rag_agent import metrics as m


def test_summarize_example():
    s = m.summarize([1, 3, None, 2])
    assert s["hit1"] == 0.25
    assert s["hit3"] == 0.75
    assert s["hit5"] == 0.75
    assert f"{s['mrr10']:.4f}" == "0.4583"


def test_rank_eleven_is_a_miss():
    ids = [f"c{i}" for i in range(1, 12)]
    assert m.first_rank(ids, {"c11"}) is None
    assert m.first_rank(ids, {"c10"}) == 10
    assert m.rr(11) == 0.0
    assert m.hit_at(11, 10) == 0.0


def test_related_only_is_a_miss():
    gold, related = {"g"}, {"r"}
    assert m.first_rank(["r", "x"], gold) is None
    assert related  # related is never passed as gold


def test_first_rank_is_one_based():
    assert m.first_rank(["a", "g", "g2"], {"g", "g2"}) == 2


def test_bootstrap_deterministic_and_contains_point():
    vals = [1.0, 0.5, 0.0, 1 / 3, 1.0, 0.0, 0.25]
    a = m.bootstrap_ci(vals)
    assert a == m.bootstrap_ci(vals)
    point = sum(vals) / len(vals)
    assert a[0] <= point <= a[1]


def test_bootstrap_all_ones():
    assert m.bootstrap_ci([1.0] * 10) == (1.0, 1.0)


def test_paired_diff_with_itself_is_zero():
    vals = [1.0, 0.0, 0.5, 0.25]
    assert m.paired_bootstrap_diff(vals, vals) == (0, 0, 0)


def test_paired_diff_uses_same_indices():
    a = [1.0, 1.0, 1.0, 1.0]
    b = [0.0, 0.0, 0.0, 0.0]
    assert m.paired_bootstrap_diff(a, b) == (1.0, 1.0, 1.0)


def test_empty_rejected():
    with pytest.raises(ValueError):
        m.summarize([])


def test_cand_recall():
    ranks = [3, 15, None, 40]
    assert [m.cand_recall(ranks, n) for n in (10, 20, 30, 50)] == [0.25, 0.5, 0.5, 0.75]


def test_auroc():
    assert m.auroc([0.9, 0.6], [0.7, 0.2]) == 0.75
    assert m.auroc([0.5, 0.5], [0.5]) == 0.5
    assert m.auroc([0.5, 0.9], [0.5]) == 0.75


def test_fallback_rates():
    assert m.fallback_rates([0.95, 0.8, 0.4], [0.3, 0.5], 0.65) == (1.0, pytest.approx(1 / 3))
    assert m.fallback_rates([0.5], [0.5], 0.5) == (0.0, 0.0)  # fallback is score < t


def test_youden_threshold():
    t, lo, hi, j = m.youden_threshold([0.95, 0.80, 0.40], [0.30, 0.50])
    assert (t, lo, hi) == (pytest.approx(0.65), 0.5, 0.8)
    assert j == pytest.approx(2 / 3, abs=1e-4)
    assert m.fallback_rates([0.95, 0.80, 0.40], [0.30, 0.50], t) == (1.0, pytest.approx(1 / 3))
    assert m.youden_threshold([0.9, 0.3], [0.2, 0.6])[0] == pytest.approx(0.75)


def test_youden_ties_go_to_higher_interval():
    # perfect separation: every threshold in (0.3, 0.7] has J = 1; (0.3, 0.7] is one interval
    t, lo, hi, j = m.youden_threshold([0.9, 0.7], [0.1, 0.3])
    assert (lo, hi, j) == (0.3, 0.7, 1.0) and t == pytest.approx(0.5)
    # two equal-J intervals: the higher one wins
    t, lo, hi, j = m.youden_threshold([0.3, 0.9], [0.1, 0.6])
    assert j == 0.5 and (lo, hi) == (0.6, 0.9) and t == pytest.approx(0.75)
