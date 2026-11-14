import pytest

from lease_clause_checker.stats import (
    count_interval,
    difference_interval,
    perfect_score_error_bound,
)

MIXED = [True] * 30 + [False] * 8


def test_a_count_interval_holds_the_count_and_stays_in_range() -> None:
    low, high = count_interval(MIXED)

    assert 0 <= low < 30 < high <= 38


def test_intervals_are_the_same_on_every_run() -> None:
    """The seed is fixed: the README's numbers can be regenerated exactly."""
    assert count_interval(MIXED) == count_interval(MIXED) == (25, 35)


def test_a_perfect_or_zero_score_has_a_one_point_interval() -> None:
    assert count_interval([True] * 38) == (38, 38)
    assert count_interval([False] * 38) == (0, 0)


def test_the_difference_interval_keeps_each_clause_s_pair_together() -> None:
    # Resampled apart, two identical columns would give a spread of differences.
    assert difference_interval(MIXED, MIXED) == (0, 0)
    low, high = difference_interval([True] * 38, MIXED)
    assert 0 < low <= 8 <= high


def test_the_difference_needs_results_on_the_same_clauses() -> None:
    with pytest.raises(ValueError, match="same clauses"):
        difference_interval(MIXED, MIXED[:-1])


def test_the_perfect_score_bound_is_the_exact_binomial_one() -> None:
    assert perfect_score_error_bound(38) == pytest.approx(0.0925, abs=1e-4)
