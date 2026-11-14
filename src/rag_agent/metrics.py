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
