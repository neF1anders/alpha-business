"""Мелкая статистика без сторонних библиотек."""
from __future__ import annotations

import math
from typing import Sequence


def percentile(values: Sequence[float], q: float) -> float:
    """Перцентиль с линейной интерполяцией, q в [0, 100]."""
    if not values:
        raise ValueError("пустая выборка")
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100
    low, high = math.floor(pos), math.ceil(pos)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Доверительный интервал Уилсона для доли; на пустой выборке (0, 1)."""
    if total == 0:
        return 0.0, 1.0
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(successes: int, total: int) -> str:
    low, high = wilson(successes, total)
    return f"{successes}/{total} = {successes / total:.1%} [{low:.1%}..{high:.1%}]" if total else "0/0"
