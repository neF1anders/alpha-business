"""Метрики качества ранжирования. Оценки судьи: 0 не важна, 1 полезно, 2 критична. Только стандартная библиотека."""
from __future__ import annotations

import math
from typing import Sequence

GAIN = {0: 0.0, 1: 1.0, 2: 3.0}                      # 2**grade - 1: критичная новость втрое ценнее полезной


def dcg(grades: Sequence[int], k: int) -> float:
    return sum(GAIN[g] / math.log2(i + 2) for i, g in enumerate(grades[:k]))


def ndcg_at_k(shown: Sequence[int], all_grades: Sequence[int], k: int) -> float:
    """`shown` - оценки показанных новостей по порядку, `all_grades` - оценки всех кандидатов (для идеала)."""
    ideal = dcg(sorted(all_grades, reverse=True), k)
    return dcg(shown, k) / ideal if ideal > 0 else 0.0


def precision_at_k(shown: Sequence[int], k: int, min_grade: int = 2) -> float:
    """Доля попаданий среди k мест; пустые места считаются промахом."""
    return sum(1 for g in shown[:k] if g >= min_grade) / k


def noise_rate(shown: Sequence[int]) -> float:
    """Доля показанных новостей с оценкой 0 (мусор в выдаче); для пустой выдачи 0."""
    return sum(1 for g in shown if g == 0) / len(shown) if shown else 0.0
