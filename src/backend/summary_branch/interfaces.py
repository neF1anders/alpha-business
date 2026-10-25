"""Интерфейсы ветки. Реализации подменяемы: в тестах заглушки, в проде LLM/Redis/Postgres."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

from .models import (
    Banner, BannerDTO, ChatAnswer, Claim, CompanyContext, Confidence, ContentKey,
    ContentSignals, SegmentSummary, SourceRef, SummaryDraft, SummaryRequest,
)


@runtime_checkable
class LLMClient(Protocol):
    """Единственная дверь к моделям (общий LLM-роутер с бюджетом)."""
    def generate_json(self, prompt: str, schema: dict, *, tier: str, trace_id: str) -> dict: ...


@runtime_checkable
class Summarizer(Protocol):
    def summarize(self, req: SummaryRequest) -> SummaryDraft:
        """S2. Один вызов LLM со структурным выводом. В банер-режиме req.news содержит один кластер."""


@runtime_checkable
class EvidenceBinder(Protocol):
    def bind(self, draft: SummaryDraft, req: SummaryRequest) -> tuple[list[Claim], list[SourceRef]]:
        """S3. Детерминированно: evidence_ids -> цитаты, отбрасывание утверждений с неизвестными ссылками,
        дедупликация, сбор источников (первоисточник + банк)."""


@runtime_checkable
class ContentScorer(Protocol):
    def score(self, req: SummaryRequest, draft: SummaryDraft, claims: list[Claim],
              sources: list[SourceRef]) -> ContentSignals:
        """S3b. Не зависит от компании. Заполняет Claim.grounding. Без LLM."""


@runtime_checkable
class ConfidenceScorer(Protocol):
    def score(self, seg: SegmentSummary, company: CompanyContext,
              now: Optional[datetime] = None) -> Confidence:
        """S5. Складывает ContentSignals сегмента с релевантностью и свежестью для конкретной компании."""


@runtime_checkable
class SegmentCache(Protocol):
    def get(self, key: ContentKey) -> Optional[SegmentSummary]: ...
    def put(self, item: SegmentSummary, ttl_s: int) -> None: ...
    def invalidate_cluster(self, cluster_id: str) -> int:
        """Удаляет сегментные саммари кластера (для всех сегментов и версий промпта). Возвращает их число."""


@runtime_checkable
class CompanyDirectory(Protocol):
    """Источник активных компаний для разворота сегментного саммари на баннеры."""
    def active_in_segment(self, segment_id: str) -> list[CompanyContext]: ...
    def get(self, company_id: str) -> Optional[CompanyContext]: ...


@runtime_checkable
class BannerComposer(Protocol):
    def compose(self, seg: SegmentSummary, company: CompanyContext, confidence: Confidence) -> Banner:
        """S4+S7. Подставляет признак компании в шаблон, ставит срок жизни по теме."""


@runtime_checkable
class BannerStore(Protocol):
    """Индекс готовых баннеров по компаниям. Чтение топ-k должно быть быстрым (десятки мс)."""
    def put(self, banner: Banner, dto: BannerDTO) -> bool:
        """Идемпотентно по banner_id. True, если баннер новый для компании и не скрыт ею (его можно пушить)."""
    def get(self, company_id: str, banner_id: str, now: Optional[datetime] = None) -> Optional[BannerDTO]:
        """Готовая выдача, если баннер есть, не просрочен и не скрыт компанией. Иначе None."""
    def top_k(self, company_id: str, k: int, now: Optional[datetime] = None) -> list[BannerDTO]: ...
    def mark_seen(self, company_id: str, banner_id: str) -> None: ...
    def suppress(self, company_id: str, banner_id: str, reason: str) -> None: ...
    def invalidate_cluster(self, cluster_id: str, reason: str) -> int: ...


@runtime_checkable
class Publisher(Protocol):
    def to_dto(self, banner: Banner, seg: SegmentSummary) -> BannerDTO: ...
    def push(self, company_id: str, banners: list[BannerDTO]) -> None:
        """SSE для открытой сессии, если баннер срочный."""


@runtime_checkable
class SummaryBranch(Protocol):
    """Публичный вход ветки для оркестратора."""
    def handle(self, req: SummaryRequest) -> list[BannerDTO] | ChatAnswer:
        """Единственный вызов оркестратора: по `req.mode` отдаёт готовые баннеры компании или ответ чата.

        Проверка качества и повтор генерации внутри ветки; оркестратор результат не оценивает.
        """
    def handle_banner(self, req: SummaryRequest) -> list[Banner]:
        """Предрасчёт: по одному баннеру на кластер из батча (после порога уверенности) для каждой компании сегмента."""
    def handle_chat(self, req: SummaryRequest) -> ChatAnswer: ...
