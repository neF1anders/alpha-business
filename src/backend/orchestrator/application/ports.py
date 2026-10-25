"""Порты оркестратора. Реализации лежат в infrastructure/ и подменяются в тестах."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

from ..domain.models import (
    Banner, ChatAnswer, CompanyProfile, EventContext, NewsItem, NewsQuery, RankedNews, RetrievalResult, SummaryRequest,
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
    async def banners(self, req: SummaryRequest) -> list[Banner]: ...
    async def chat(self, req: SummaryRequest) -> ChatAnswer: ...


@runtime_checkable
class EventRetriever(Protocol):
    """A concrete news event + company -> dated bank context and matching trace."""
    async def retrieve(self, event: NewsItem, profile: CompanyProfile, *,
                       as_of: Optional[datetime] = None, limit: int = 5) -> EventContext: ...
