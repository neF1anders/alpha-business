"""Исключения предметной области ветки."""


class BranchError(Exception):
    """Базовая ошибка ветки саммари."""


class SummarizationFailed(BranchError):
    """Модель не вернула валидный SummaryDraft (в том числе после повтора)."""


class BudgetExhausted(BranchError):
    """Бюджет в режиме CACHED_ONLY: вызывать модель запрещено."""
