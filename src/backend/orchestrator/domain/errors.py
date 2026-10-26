"""Ошибки оркестратора. Транспортный слой превращает их в HTTP-коды и сообщения WebSocket."""


class OrchestratorError(Exception):
    """Базовая ошибка."""


class CompanyNotFound(OrchestratorError):
    def __init__(self, company_id: str) -> None:
        super().__init__(f"компания {company_id!r} не найдена")
        self.company_id = company_id


class DependencyUnavailable(OrchestratorError):
    """Внешний компонент (retriever, ветка саммари, БД) не ответил или упал."""

    def __init__(self, name: str, reason: str = "") -> None:
        super().__init__(f"{name} недоступен" + (f": {reason}" if reason else ""))
        self.name = name


class DeadlineExceeded(OrchestratorError):
    """Не уложились в SLO запроса."""
