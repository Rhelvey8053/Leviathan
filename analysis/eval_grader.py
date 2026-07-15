"""
analysis/eval_grader.py — Deterministic grader for Leviathan's eval harness.

No model in the loop. Takes (estimate, actual_outcome_binary) pairs and
returns Brier score, hit rate at the 0.5 threshold, and calibration broken
down by estimate decile. Pure arithmetic — reused by analysis/eval.py to
grade the scorer's own estimates, the market price, and a constant 0.5
baseline on the same frozen dataset.
"""

from __future__ import annotations


def brier_score(pairs: list[tuple[float, int]]) -> float | None:
    """
    Mean squared error between each estimate (probability of YES) and the
    actual binary outcome (1 = market resolved YES, 0 = resolved NO).

    Lower is better. Perfect calibration = 0. Random 50/50 = 0.25.
    """
    if not pairs:
        return None
    return sum((est - actual) ** 2 for est, actual in pairs) / len(pairs)


def hit_rate(pairs: list[tuple[float, int]], threshold: float = 0.5) -> float | None:
    """
    Fraction of pairs where the binary prediction implied by `estimate`
    (YES if estimate >= threshold, else NO) matches the actual outcome.
    """
    if not pairs:
        return None
    hits = sum(
        1 for est, actual in pairs
        if (1 if est >= threshold else 0) == actual
    )
    return hits / len(pairs)


def calibration_by_decile(pairs: list[tuple[float, int]]) -> list[dict]:
    """
    Buckets pairs into ten deciles by estimate ([0.0-0.1), ..., [0.9-1.0]),
    reporting n, mean estimate, and actual YES rate per non-empty bucket.
    Well-calibrated buckets have mean_estimate close to actual_rate.
    """
    buckets: dict[int, list[tuple[float, int]]] = {}
    for est, actual in pairs:
        idx = min(int(est * 10), 9)  # estimate==1.0 falls into the last bucket
        buckets.setdefault(idx, []).append((est, actual))

    result = []
    for idx in sorted(buckets):
        bucket_pairs = buckets[idx]
        n = len(bucket_pairs)
        mean_estimate = sum(est for est, _ in bucket_pairs) / n
        actual_rate = sum(actual for _, actual in bucket_pairs) / n
        result.append({
            "range": f"{idx / 10:.1f}-{(idx + 1) / 10:.1f}",
            "n": n,
            "mean_estimate": round(mean_estimate, 4),
            "actual_rate": round(actual_rate, 4),
        })
    return result


def grade(pairs: list[tuple[float, int]]) -> dict:
    """Full grading report for a set of (estimate, actual_outcome_binary) pairs."""
    return {
        "n": len(pairs),
        "brier": brier_score(pairs),
        "hit_rate": hit_rate(pairs),
        "calibration_by_decile": calibration_by_decile(pairs),
    }
