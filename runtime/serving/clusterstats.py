"""Cluster bootstrap over sessions, because requests are not independent.

Requests inside one loaded session share a kernel cache, an allocator state, a
thermal state and one VM lifetime, so their errors are correlated. Treating them
as independent replicates shrinks a confidence interval by roughly the square
root of the request count and manufactures significance that is not there.

The resampling unit is therefore the **session**. A bootstrap draw takes
sessions with replacement, and within each drawn session takes its requests with
replacement, which preserves both levels of variation.

Within-session and between-session variance are reported separately rather than
pooled: they answer different questions. Within-session spread says how noisy a
measurement is once a runtime is warm. Between-session spread says how much a
result depends on which launch produced it, and it is the term that decides how
many sessions a comparison needs.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100.0
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def _variance(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = _mean(values)
    return sum((v - average) ** 2 for v in values) / (len(values) - 1)


def variance_components(sessions: Sequence[Sequence[float]]) -> Dict[str, Any]:
    """Within- and between-session variation, kept apart."""
    usable = [list(s) for s in sessions if s]
    if not usable:
        return {}
    session_means = [_mean(s) for s in usable]
    within = [_variance(s) for s in usable if len(s) > 1]
    between_var = _variance(session_means)
    within_var = _mean(within) if within else 0.0
    total = within_var + between_var
    return {
        "sessions": len(usable),
        "requestsPerSession": [len(s) for s in usable],
        "sessionMeans": [round(v, 4) for v in session_means],
        "withinSessionStdDev": round(math.sqrt(within_var), 4),
        "betweenSessionStdDev": round(math.sqrt(between_var), 4),
        # How much of the total variation comes from which launch produced the
        # number. A high share means single-session results are not portable.
        "betweenSessionShare": round(between_var / total, 4) if total > 0 else None,
    }


def cluster_bootstrap(sessions: Sequence[Sequence[float]], statistic=percentile,
                      pct: float = 50.0, draws: int = 5000,
                      seed: int = 20260826) -> Dict[str, Any]:
    """p50 and a 95% interval, resampling sessions rather than requests."""
    usable = [list(s) for s in sessions if s]
    if len(usable) < 2:
        flat = [v for s in usable for v in s]
        return {
            "point": round(statistic(flat, pct), 4) if flat else None,
            "ci95": None,
            "sessions": len(usable),
            "note": "fewer than two sessions; no interval is computable",
        }

    flat = [v for s in usable for v in s]
    point = statistic(flat, pct)
    rng = random.Random(seed)
    estimates: List[float] = []
    count = len(usable)
    for _ in range(draws):
        pooled: List[float] = []
        for _ in range(count):
            chosen = usable[rng.randrange(count)]
            # Resample within the drawn session too, so both levels vary.
            pooled.extend(chosen[rng.randrange(len(chosen))]
                          for _ in range(len(chosen)))
        if pooled:
            estimates.append(statistic(pooled, pct))
    estimates.sort()
    return {
        "point": round(point, 4),
        "ci95": [round(percentile(estimates, 2.5), 4),
                 round(percentile(estimates, 97.5), 4)],
        "sessions": count,
        "observations": len(flat),
        "draws": draws,
        "seed": seed,
    }


def summarise(sessions: Sequence[Sequence[float]], draws: int = 5000
              ) -> Dict[str, Any]:
    """The full picture for one metric: point, interval, and both variances."""
    report = cluster_bootstrap(sessions, draws=draws)
    report["variance"] = variance_components(sessions)
    flat = [v for s in sessions for v in s]
    if flat:
        report["p95"] = round(percentile(flat, 95), 4)
        report["p99"] = round(percentile(flat, 99), 4)
        report["min"] = round(min(flat), 4)
        report["max"] = round(max(flat), 4)
    return report
