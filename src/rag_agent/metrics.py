"""Retrieval metrics. Pure functions: no I/O, no rounding (callers print 4 decimals)."""

from __future__ import annotations

import random
from collections.abc import Collection, Sequence

SEED = 20261111


def first_rank(ranked_ids: Sequence[str], gold: Collection[str], cutoff: int = 10) -> int | None:
    """1-based rank of the first gold id within the first `cutoff` items, else None."""
    for i, cid in enumerate(ranked_ids[:cutoff], start=1):
        if cid in gold:
            return i
    return None


def hit_at(rank: int | None, k: int) -> float:
    return 1.0 if rank is not None and rank <= k else 0.0


def rr(rank: int | None, cutoff: int = 10) -> float:
    return 1.0 / rank if rank is not None and rank <= cutoff else 0.0


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def summarize(ranks: Sequence[int | None]) -> dict[str, float]:
    if not ranks:
        raise ValueError("no ranks")
    return {
        "hit1": _mean([hit_at(r, 1) for r in ranks]),
        "hit3": _mean([hit_at(r, 3) for r in ranks]),
        "hit5": _mean([hit_at(r, 5) for r in ranks]),
        "mrr10": _mean([rr(r) for r in ranks]),
    }


def _bounds(means: list[float], alpha: float) -> tuple[float, float]:
    means.sort()
    n = len(means)
    lo = means[int((alpha / 2) * n)]
    hi = means[min(n - 1, int((1 - alpha / 2) * n) - 1)]
    return lo, hi


def bootstrap_ci(
    values: Sequence[float], n: int = 2000, seed: int = SEED, alpha: float = 0.05
) -> tuple[float, float]:
    """Percentile interval of the means of `n` resamples (with replacement)."""
    if not values:
        raise ValueError("no values")
    rng = random.Random(seed)
    m = len(values)
    means = [_mean([values[rng.randrange(m)] for _ in range(m)]) for _ in range(n)]
    return _bounds(means, alpha)


def paired_bootstrap_diff(
    a: Sequence[float],
    b: Sequence[float],
    n: int = 2000,
    seed: int = SEED,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """(mean(a-b), lo, hi); one set of resample indices per iteration is used for both lists."""
    if len(a) != len(b) or not a:
        raise ValueError("lists must be non-empty and of equal length")
    rng = random.Random(seed)
    m = len(a)
    diffs = [x - y for x, y in zip(a, b, strict=True)]
    means = []
    for _ in range(n):
        idx = [rng.randrange(m) for _ in range(m)]
        means.append(_mean([a[i] for i in idx]) - _mean([b[i] for i in idx]))
    lo, hi = _bounds(means, alpha)
    return _mean(diffs), lo, hi


def cand_recall(first_ranks: Sequence[int | None], n: int) -> float:
    """Fraction of questions whose best gold rank among the candidates is <= n (None = absent)."""
    if not first_ranks:
        raise ValueError("no ranks")
    return _mean([1.0 if r is not None and r <= n else 0.0 for r in first_ranks])


def auroc(pos: Sequence[float], neg: Sequence[float]) -> float:
    """P(pos > neg) over all pos/neg pairs; ties count 1/2."""
    if not pos or not neg:
        raise ValueError("need positives and negatives")
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def fallback_rates(pos: Sequence[float], neg: Sequence[float], t: float) -> tuple[float, float]:
    """(correct_fallback_rate, false_fallback_rate); fallback means score < t.

    pos = answerable top-1 scores, neg = out-of-scope top-1 scores. correct = share of neg that
    fall back; false = share of pos that fall back.
    """
    if not pos or not neg:
        raise ValueError("need positives and negatives")
    correct = sum(1 for s in neg if s < t) / len(neg)
    false = sum(1 for s in pos if s < t) / len(pos)
    return correct, false


def youden_threshold(
    pos: Sequence[float], neg: Sequence[float]
) -> tuple[float, float, float, float]:
    """(t, lo, hi, j): the interval (lo, hi] of thresholds maximizing correct - false.

    Candidate intervals from the sorted unique scores s1..sk: (0, s1], (s1, s2], ..., (sk, 1].
    Every t in (lo, hi] gives the same rates (score < t). Ties go to the interval with the
    higher bounds; t is the midpoint (lo + hi) / 2.
    """
    if not pos or not neg:
        raise ValueError("need positives and negatives")
    s = sorted(set(pos) | set(neg))
    bounds = [0.0, *s, 1.0]
    best: tuple[float, float, float] | None = None
    for lo, hi in zip(bounds, bounds[1:], strict=False):
        if hi <= lo:  # a score of exactly 0 or 1 leaves an empty interval
            continue
        t = (lo + hi) / 2
        correct, false = fallback_rates(pos, neg, t)
        j = correct - false
        if best is None or j >= best[0]:
            best = (j, lo, hi)
    assert best is not None
    j, lo, hi = best
    return (lo + hi) / 2, lo, hi, j
