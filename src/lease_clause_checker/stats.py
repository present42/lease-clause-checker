"""Bootstrap intervals for counts over a small test set.

Each clause is one draw; an interval comes from resampling the clauses with replacement and
recounting. The seed is fixed, so the same answers always give the same intervals.
"""

import math
import random
from collections.abc import Sequence

RESAMPLES = 10_000
SEED = 0
LEVEL = 0.95


def count_interval(hits: Sequence[bool]) -> tuple[int, int]:
    """A 95% percentile-bootstrap interval for the number of hits."""
    return _percentiles([sum(hits[i] for i in draw) for draw in _draws(len(hits))])


def difference_interval(first: Sequence[bool], second: Sequence[bool]) -> tuple[int, int]:
    """A 95% paired-bootstrap interval for (hits in `first`) minus (hits in `second`).

    `first[i]` and `second[i]` are two methods' results on the same clause; resampling clauses
    keeps the pairs together.
    """
    if len(first) != len(second):
        raise ValueError("paired results must cover the same clauses")
    pairs = [int(a) - int(b) for a, b in zip(first, second, strict=True)]
    return _percentiles([sum(pairs[i] for i in draw) for draw in _draws(len(pairs))])


def perfect_score_error_bound(n: int) -> float:
    """The largest error rate consistent (exact binomial, one-sided 97.5%) with n of n right.

    A bootstrap of a perfect score only ever redraws hits, so its interval is a single point;
    this bound says how far a perfect score on n clauses still is from proving no errors.
    """
    return 1 - math.pow((1 - LEVEL) / 2, 1 / n)


def _draws(n: int) -> list[list[int]]:
    if n == 0:
        raise ValueError("no results to resample")
    rng = random.Random(SEED)
    return [rng.choices(range(n), k=n) for _ in range(RESAMPLES)]


def _percentiles(totals: list[int]) -> tuple[int, int]:
    totals.sort()
    tail = (1 - LEVEL) / 2
    return totals[math.floor(tail * len(totals))], totals[math.ceil((1 - tail) * len(totals)) - 1]
