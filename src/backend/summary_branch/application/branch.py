"""Публичный вход ветки: баннер компании по кластеру и ответ чата.

Баннер: готовый из хранилища -> разбор кластера (общий для всех, кэш) -> пути влияния на компанию ->
уверенность -> персональный текст -> сохранение и отправка подписчику. Чат: те же пути влияния идут в промпт как
признаки компании, в хранилище ничего не пишется.
"""
from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from ..config import BranchConfig
from ..domain.errors import BudgetExhausted, SummarizationFailed
from ..domain.exposure import exposure_paths, news_exposure_paths, placements_for
from ..domain.models import (
    BannerDTO, ChatAnswer, CompanyContext, Decision, ExposurePath, Mode, RankedNewsItem, SourceRef, SummaryRequest,
)
from ..domain.personalization import enrich
from ..domain.ports import BannerStore, ConfidenceScorer, Publisher
from ..domain.scoring import company_confidence, lead_from_news
from .cluster_summary import ClusterSummaryBuilder
from .composer import DefaultBannerComposer, make_banner_id
from .generator import ValidatedGenerator
from .personal import PersonalWriter

log = logging.getLogger(__name__)


def _lead_source(news: RankedNewsItem) -> SourceRef:
    return SourceRef(kind=news.source_kind, title=news.title, url=news.url, ref_id=news.news_id)


class DefaultSummaryBranch:
    def __init__(self, *, cluster_summary: ClusterSummaryBuilder, confidence_scorer: ConfidenceScorer,
                 personal_writer: PersonalWriter, composer: DefaultBannerComposer, publisher: Publisher,
                 store: BannerStore, generator: ValidatedGenerator, config: BranchConfig | None = None) -> None:
        self._cluster_summary = cluster_summary
        self._confidence_scorer = confidence_scorer
        self._personal_writer = personal_writer
        self._composer = composer
        self._publisher = publisher
        self._store = store
        self._generator = generator
        self._config = config or BranchConfig()

    # ------------------------------------------------------------------ вход оркестратора
    def handle(self, req: SummaryRequest) -> list[BannerDTO] | ChatAnswer:
        return self.handle_chat(req) if req.mode == Mode.CHAT else self.serve_banners(req)

    def serve_banners(self, req: SummaryRequest) -> list[BannerDTO]:
        """Кластеры параллельно: основное время уходит на LLM. Порядок кластеров сохраняется."""
        clusters = self._top_clusters(req)
        workers = min(len(clusters), self._config.max_parallel_clusters)
        requests = [req.model_copy(update={"news": items}) for items in clusters]
        if workers <= 1:
            results = [self.banner(r) for r in requests]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(self.banner, requests))
        return [dto for dto in results if dto is not None]

    def banner(self, req: SummaryRequest) -> Optional[BannerDTO]:
        req = self._enriched(req)
        cluster = self._top_clusters(req)[0]
        req = req.model_copy(update={"news": cluster})
        company = req.company
        banner_id = make_banner_id(company.company_id, self._cluster_summary.key_for(cluster[0].cluster_id))
        ready = self._store.get(company.company_id, banner_id)
        if ready is not None or self._store.is_hidden(company.company_id, banner_id):
            log.info("баннер из хранилища: trace_id=%s company=%s banner=%s hidden=%s", req.trace_id,
                     company.company_id, banner_id, ready is None)
            return ready
        try:
            summary = self._cluster_summary.build(req)
        except (SummarizationFailed, BudgetExhausted) as exc:
            log.warning("кластер пропущен: trace_id=%s cluster=%s reason=%s: %s",
                        req.trace_id, cluster[0].cluster_id, type(exc).__name__, exc)
            return None
        if summary is None:
            return None

        paths = exposure_paths(summary, company)
        confidence = self._confidence_scorer.score(summary, company, paths=paths if company.profile else None)
        log.info("уверенность: trace_id=%s key=%s company=%s score=%.3f decision=%s paths=%s reasons=%s",
                 req.trace_id, summary.key, company.company_id, confidence.score, confidence.decision.value,
                 [(p.kind.value, p.weight) for p in paths], confidence.reasons)
        if confidence.decision == Decision.DROP:
            return None
        personal = self._personal_writer.write(summary, company, paths, role=req.role, trace_id=req.trace_id,
                                               budget=req.budget)
        banner = self._composer.compose(summary, company, confidence, personal)
        used = [p for p in paths if p.path_id in personal.path_ids] or paths[:1]
        dto = self._publisher.to_dto(banner, summary, placements_for(used))
        if self._store.put(banner, dto):
            self._publisher.push(company.company_id, [dto])
        log.info("баннер сохранён: trace_id=%s banner=%s company=%s decision=%s personal_model=%s", req.trace_id,
                 banner.banner_id, company.company_id, banner.decision.value, personal.from_model)
        return dto

    @staticmethod
    def _enriched(req: SummaryRequest) -> SummaryRequest:
        """Добавляет к тегам и признакам компании выведенные из её профиля."""
        return req.model_copy(update={"company": enrich(req.company)})

    def _top_clusters(self, req: SummaryRequest) -> list[list[RankedNewsItem]]:
        by_cluster: dict[str, list[RankedNewsItem]] = {}
        for item in req.news:
            by_cluster.setdefault(item.cluster_id, []).append(item)
        ranked = sorted(by_cluster.values(), key=lambda items: max(n.rerank_score for n in items), reverse=True)
        return ranked[: self._config.max_clusters_per_request]

    # ------------------------------------------------------------------ chat
    def handle_chat(self, req: SummaryRequest) -> ChatAnswer:
        req = self._enriched(req)
        lead = req.news[0]
        paths = self._chat_paths(req.company, lead)
        req = req.model_copy(update={"company": _with_path_features(req.company, paths)})
        try:
            content = self._generator.generate(req)
        except (SummarizationFailed, BudgetExhausted) as exc:
            log.warning("чат: сырая новость: trace_id=%s reason=%s", req.trace_id, type(exc).__name__)
            return self._raw_news_answer(lead, confidence=0.0)

        draft, claims, sources, signals = content.draft, content.claims, content.sources, content.signals
        confidence = company_confidence(signals, lead_from_news(lead), draft.topic, req.company,
                                        has_sources=bool(sources), severity=draft.severity, requested_by_user=True,
                                        paths=paths or None)
        log.info("чат: trace_id=%s score=%.3f decision=%s paths=%d reasons=%s",
                 req.trace_id, confidence.score, confidence.decision.value, len(paths), confidence.reasons)
        if confidence.decision in (Decision.SHOW_RAW_NEWS, Decision.DROP) or not signals.structure_ok:
            return self._raw_news_answer(lead, confidence=confidence.score)
        return ChatAnswer(answer_id=uuid.uuid4().hex, claims=claims, sources=sources,
                          caveat=confidence.decision == Decision.SHOW_WITH_CAVEAT,
                          follow_ups=draft.follow_ups, confidence=confidence.score)

    def _chat_paths(self, company: CompanyContext, lead: RankedNewsItem) -> list[ExposurePath]:
        """Карта влияния берётся из кэша кластера, если баннер по нему уже собирали; иначе пути по тегам новости."""
        summary = self._cluster_summary.cached(lead.cluster_id)
        return exposure_paths(summary, company) if summary is not None else news_exposure_paths(company, lead.tags)

    @staticmethod
    def _raw_news_answer(lead: RankedNewsItem, confidence: float) -> ChatAnswer:
        return ChatAnswer(answer_id=uuid.uuid4().hex, claims=[], sources=[_lead_source(lead)],
                          raw_news=True, confidence=confidence)


def _with_path_features(company: CompanyContext, paths: list[ExposurePath]) -> CompanyContext:
    """Пути влияния — самые точные признаки компании для вывода в чате: идут первыми в блок COMPANY features."""
    features = list(dict.fromkeys([*(p.explanation for p in paths), *company.features]))
    return company.model_copy(update={"features": features})
