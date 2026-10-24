"""Публичный вход ветки: предрасчёт баннеров и ответ чата."""
from __future__ import annotations

import logging
import uuid
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from .composer import make_banner_id
from .config import BranchConfig
from .errors import BudgetExhausted, SummarizationFailed
from .generator import ValidatedGenerator
from .interfaces import (
    BannerComposer, BannerStore, CompanyDirectory, ConfidenceScorer, Publisher,
)
from .personalization import enrich
from .models import (
    Banner, BannerDTO, ChatAnswer, CompanyContext, ContentKey, Decision, Mode, RankedNewsItem, SegmentSummary,
    Severity, SourceRef, SummaryRequest,
)
from .scoring import company_confidence, lead_from_news
from .segment_builder import SegmentBuilder

log = logging.getLogger(__name__)


def _lead_source(news: RankedNewsItem) -> SourceRef:
    return SourceRef(kind=news.source_kind, title=news.title, url=news.url)


class DefaultSummaryBranch:
    def __init__(self, *, segment_builder: SegmentBuilder, directory: CompanyDirectory,
                 confidence_scorer: ConfidenceScorer, composer: BannerComposer, publisher: Publisher,
                 store: BannerStore, generator: ValidatedGenerator, config: BranchConfig | None = None) -> None:
        self._segment_builder = segment_builder
        self._directory = directory
        self._confidence_scorer = confidence_scorer
        self._composer = composer
        self._publisher = publisher
        self._store = store
        self._generator = generator
        self._config = config or BranchConfig()

    # ------------------------------------------------------------------ вход оркестратора
    def handle(self, req: SummaryRequest) -> list[BannerDTO] | ChatAnswer:
        return self.handle_chat(req) if req.mode == Mode.CHAT else self.serve_banners(req)

    @staticmethod
    def _enriched(req: SummaryRequest) -> SummaryRequest:
        """Добавляет к тегам и признакам компании выведенные из её профиля."""
        return req.model_copy(update={"company": enrich(req.company)})

    def serve_banners(self, req: SummaryRequest) -> list[BannerDTO]:
        """Баннеры компании запроса: готовые из хранилища отдаются сразу, недостающие генерируются и сохраняются."""
        req = self._enriched(req)
        clusters = self._top_clusters(req)
        company_id = req.company.company_id
        ids = [self._banner_id(req.company, items) for items in clusters]
        ready = {banner_id: self._store.get(company_id, banner_id) for banner_id in ids}
        missing = [items for items, banner_id in zip(clusters, ids) if ready[banner_id] is None]
        if missing:
            log.info("баннеры: trace_id=%s company=%s готово=%d генерируем=%d", req.trace_id, company_id,
                     len(clusters) - len(missing), len(missing))
            self.handle_banner(req.model_copy(update={"news": [n for items in missing for n in items]}))
            ready = {banner_id: ready[banner_id] or self._store.get(company_id, banner_id) for banner_id in ids}
        return [dto for dto in ready.values() if dto is not None]

    def _banner_id(self, company: CompanyContext, cluster_news: list[RankedNewsItem]) -> str:
        key = ContentKey(cluster_id=cluster_news[0].cluster_id, segment_id=company.segment_id,
                         prompt_version=self._config.prompt_version)
        return make_banner_id(company.company_id, key)

    # ------------------------------------------------------------------ banner
    def handle_banner(self, req: SummaryRequest) -> list[Banner]:
        req = self._enriched(req)
        companies = self._segment_companies(req.company)
        created: list[Banner] = []
        for seg in self._build_segments(req, self._top_clusters(req)):
            if seg is None:
                continue
            for company in companies:
                banner = self._publish_for_company(req.trace_id, seg, company)
                if banner is not None:
                    created.append(banner)
        return created

    def _build_segments(self, req: SummaryRequest,
                        clusters: list[list[RankedNewsItem]]) -> list[Optional[SegmentSummary]]:
        """Саммари кластеров считаются параллельно (основное время уходит на LLM); порядок результата сохраняется."""
        workers = min(len(clusters), self._config.max_parallel_clusters)
        if workers <= 1:
            return [self._build_segment(req, news) for news in clusters]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda news: self._build_segment(req, news), clusters))

    def _build_segment(self, req: SummaryRequest, cluster_news: list[RankedNewsItem]) -> Optional[SegmentSummary]:
        cluster_id = cluster_news[0].cluster_id
        try:
            seg = self._segment_builder.build(req.model_copy(update={"news": cluster_news}))
        except (SummarizationFailed, BudgetExhausted) as exc:
            log.warning("кластер пропущен: trace_id=%s cluster=%s reason=%s: %s",
                        req.trace_id, cluster_id, type(exc).__name__, exc)
            return None
        if seg is None:
            log.warning("кластер пропущен: trace_id=%s cluster=%s reason=пустое саммари", req.trace_id, cluster_id)
        return seg

    def _top_clusters(self, req: SummaryRequest) -> list[list[RankedNewsItem]]:
        by_cluster: dict[str, list[RankedNewsItem]] = {}
        for item in req.news:
            by_cluster.setdefault(item.cluster_id, []).append(item)
        ranked = sorted(by_cluster.values(), key=lambda items: max(n.rerank_score for n in items), reverse=True)
        return ranked[: self._config.max_clusters_per_request]

    def _segment_companies(self, representative: CompanyContext) -> list[CompanyContext]:
        companies = {representative.company_id: representative}
        for company in self._directory.active_in_segment(representative.segment_id):
            companies.setdefault(company.company_id, enrich(company))
        return list(companies.values())

    def _publish_for_company(self, trace_id, seg, company) -> Banner | None:
        confidence = self._confidence_scorer.score(seg, company)
        log.info("уверенность: trace_id=%s key=%s company=%s score=%.3f decision=%s reasons=%s",
                 trace_id, seg.key, company.company_id, confidence.score, confidence.decision.value,
                 confidence.reasons)
        if confidence.decision == Decision.DROP:
            return None
        banner = self._composer.compose(seg, company, confidence)
        dto = self._publisher.to_dto(banner, seg)
        is_new = self._store.put(banner, dto)
        log.info("баннер сохранён: trace_id=%s banner=%s company=%s new=%s", trace_id, banner.banner_id,
                 company.company_id, is_new)
        is_urgent = (is_new and banner.severity == Severity.ATTENTION and banner.decision == Decision.SHOW_SUMMARY
                     and confidence.score >= self._config.urgent_min_score)
        if is_urgent:
            self._publisher.push(company.company_id, [dto])
            log.info("срочный пуш: trace_id=%s banner=%s company=%s", trace_id, banner.banner_id, company.company_id)
        return banner

    # ------------------------------------------------------------------ chat
    def handle_chat(self, req: SummaryRequest) -> ChatAnswer:
        req = self._enriched(req)
        lead = req.news[0]
        try:
            content = self._generator.generate(req)
        except (SummarizationFailed, BudgetExhausted) as exc:
            log.warning("чат: сырая новость: trace_id=%s reason=%s", req.trace_id, type(exc).__name__)
            return self._raw_news_answer(lead, confidence=0.0)

        draft, claims, sources, signals = content.draft, content.claims, content.sources, content.signals
        confidence = company_confidence(signals, lead_from_news(lead), draft.topic, req.company,
                                        has_sources=bool(sources), severity=draft.severity, requested_by_user=True)
        log.info("чат: trace_id=%s score=%.3f decision=%s reasons=%s",
                 req.trace_id, confidence.score, confidence.decision.value, confidence.reasons)
        if confidence.decision in (Decision.SHOW_RAW_NEWS, Decision.DROP) or not signals.structure_ok:
            return self._raw_news_answer(lead, confidence=confidence.score)
        return ChatAnswer(answer_id=uuid.uuid4().hex, claims=claims, sources=sources,
                          caveat=confidence.decision == Decision.SHOW_WITH_CAVEAT,
                          follow_ups=draft.follow_ups, confidence=confidence.score)

    @staticmethod
    def _raw_news_answer(lead: RankedNewsItem, confidence: float) -> ChatAnswer:
        return ChatAnswer(answer_id=uuid.uuid4().hex, claims=[], sources=[_lead_source(lead)],
                          raw_news=True, confidence=confidence)
