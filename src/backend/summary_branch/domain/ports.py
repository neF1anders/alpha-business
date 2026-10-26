"""Интерфейсы ветки. Реализации подменяемы: в тестах заглушки, в проде LLM/Redis/Postgres."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

from .models import (
    Banner, BannerDTO, ChatAnswer, Claim, ClusterSummary, CompanyContext, Confidence, ContentKey, ContentSignals,
    ExposurePath, SourceRef, SummaryDraft, SummaryRequest,
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
    def score(self, seg: ClusterSummary, company: CompanyContext, now: Optional[datetime] = None,
              paths: Optional[list[ExposurePath]] = None) -> Confidence:
        """S5. Складывает ContentSignals кластера с персональной чувствительностью (пути влияния) и свежестью."""


@runtime_checkable
class SummaryCache(Protocol):
    def get(self, key: ContentKey) -> Optional[ClusterSummary]: ...
    def put(self, item: ClusterSummary, ttl_s: int) -> None: ...
    def invalidate_cluster(self, cluster_id: str) -> int:
        """Удаляет саммари кластера (для всех версий промпта). Возвращает их число."""


@runtime_checkable
class BannerStore(Protocol):
    """Индекс готовых баннеров по компаниям. Чтение топ-k должно быть быстрым (десятки мс)."""
    def put(self, banner: Banner, dto: BannerDTO) -> bool:
        """Идемпотентно по banner_id. True, если баннер новый для компании и не скрыт ею (его можно пушить)."""
    def get(self, company_id: str, banner_id: str, now: Optional[datetime] = None) -> Optional[BannerDTO]:
        """Готовая выдача, если баннер есть, не просрочен и не скрыт компанией. Иначе None."""
    def is_hidden(self, company_id: str, banner_id: str) -> bool:
        """Компания убрала баннер («не про меня» или «скрыть»): генерировать его заново не нужно."""
    def top_k(self, company_id: str, k: int, now: Optional[datetime] = None) -> list[BannerDTO]: ...
    def counts(self, company_id: str, now: Optional[datetime] = None) -> dict[str, int]:
        """Число актуальных баннеров по severity и скрытых (`hidden`): для индикаторов панели, а не только top-k."""
    def mark_seen(self, company_id: str, banner_id: str) -> None: ...
    def suppress(self, company_id: str, banner_id: str, reason: str) -> None:
        """«Не про меня»: негативная обратная связь, баннер больше не показывается."""
    def hide(self, company_id: str, banner_id: str) -> None:
        """«Скрыть»: убрать из выдачи без негативной обратной связи; попадает в счётчик `hidden`."""
    def invalidate_cluster(self, cluster_id: str, reason: str) -> int: ...


@runtime_checkable
class Publisher(Protocol):
    def to_dto(self, banner: Banner, seg: ClusterSummary, placements: list[str] | None = None) -> BannerDTO: ...
    def push(self, company_id: str, banners: list[BannerDTO]) -> None:
        """Новый баннер компании уходит подписчику (SSE открытой сессии)."""


@runtime_checkable
class SummaryBranch(Protocol):
    """Публичный вход ветки для оркестратора."""
    def banner(self, req: SummaryRequest) -> Optional[BannerDTO]:
        """Баннер компании по одному кластеру: готовый из хранилища или собранный сейчас и сохранённый.

        None — новость компании не показываем (низкая связь, скрыта компанией или модель не смогла разобрать кластер).
        """
    def serve_banners(self, req: SummaryRequest) -> list[BannerDTO]:
        """Баннеры по нескольким кластерам запроса, кластеры параллельно."""
    def handle_chat(self, req: SummaryRequest) -> ChatAnswer: ...
    def handle(self, req: SummaryRequest) -> list[BannerDTO] | ChatAnswer:
        """По `req.mode`: баннеры компании или ответ чата. Проверка качества и повтор генерации внутри ветки."""
