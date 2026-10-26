"""Порты оркестратора. Реализации лежат в infrastructure/ и подменяются в тестах."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

from ..domain.models import (
    Banner, ChatAnswer, CompanyDossier, CompanyProfile, EventContext, NewsItem, NewsQuery, RankedNews, RetrievalResult,
    SignalEvent, SummaryRequest,
)


@runtime_checkable
class ProfileRepository(Protocol):
    async def get(self, company_id: str) -> Optional[CompanyProfile]: ...


@runtime_checkable
class NewsRetriever(Protocol):
    """Новости и материалы банка по тегам компании и/или вопросу. Реализует команда retrieval."""
    async def retrieve(self, query: NewsQuery) -> RetrievalResult: ...


@runtime_checkable
class Reranker(Protocol):
    """Синхронный и CPU-bound: use case вызывает его через to_thread."""
    def rerank(self, profile: CompanyProfile, items: list[NewsItem], limit: int, *,
               require_tag_match: bool = True, now: Optional[datetime] = None) -> list[RankedNews]: ...


@runtime_checkable
class SummaryBranch(Protocol):
    """Ветка саммари: кэш баннеров, генерация, валидация. Оркестратор результат не оценивает."""
    async def banner(self, req: SummaryRequest) -> Optional[Banner]:
        """Баннер по одной новости запроса: готовый или собранный сейчас. None — новость компании не показываем.

        Если вызов не уложился в срок, ветка доводит баннер в фоне и отдаёт его через SignalSink.
        """
    async def chat(self, req: SummaryRequest) -> ChatAnswer: ...


@runtime_checkable
class CompanyDossierSource(Protocol):
    """Профиль, платёжный граф и связанные компании из данных банка (только чтение)."""
    async def get(self, company_id: str) -> Optional[CompanyDossier]: ...


@runtime_checkable
class BannerFeed(Protocol):
    """Сохранённые баннеры компании и реакции на них. Быстрое чтение без генерации."""
    async def live(self, company_id: str, limit: int) -> list[Banner]: ...
    async def get(self, company_id: str, banner_id: str) -> Optional[Banner]: ...
    async def hidden_count(self, company_id: str) -> int: ...
    async def mark_seen(self, company_id: str, banner_id: str) -> None: ...
    async def suppress(self, company_id: str, banner_id: str, reason: str) -> None: ...
    async def hide(self, company_id: str, banner_id: str) -> None: ...


@runtime_checkable
class SignalSink(Protocol):
    """Доставка событий открытой сессии клиента. Транспорт (SSE, WebSocket, очередь) скрыт за портом."""
    def publish_banners(self, company_id: str, banners: list[Banner]) -> None:
        """Новые баннеры; баннер заменяет «только новость» своего кластера."""
    def publish_invalidate(self, company_id: str, banner_ids: list[str]) -> None: ...


@runtime_checkable
class EventRetriever(Protocol):
    """A concrete news event + company -> dated bank context and matching trace."""
    async def retrieve(self, event: NewsItem, profile: CompanyProfile, *,
                       as_of: Optional[datetime] = None, limit: int = 5) -> EventContext: ...


@runtime_checkable
class SignalSubscription(Protocol):
    async def next(self) -> SignalEvent:
        """Ждёт следующее событие; отмена ожидания подписку не закрывает."""
    def close(self) -> None: ...


@runtime_checkable
class SignalStream(Protocol):
    """Подписка открытой сессии на события компании: читает интерфейсный слой (SSE, WebSocket)."""
    def subscribe(self, company_id: str) -> SignalSubscription: ...
