"""Публичный вход ветки: предрасчёт баннеров и ответ чата."""
from __future__ import annotations

import logging
import uuid

from .config import BranchConfig
from .errors import BudgetExhausted, SummarizationFailed
from .interfaces import (
    BannerComposer, BannerStore, CompanyDirectory, ConfidenceScorer, ContentScorer, EvidenceBinder, Publisher,
    Summarizer,
)
from .models import (
    Banner, ChatAnswer, CompanyContext, Decision, RankedNewsItem, Severity, SourceRef, SummaryRequest,
)
from .scoring import company_confidence, lead_from_news
from .segment_builder import SegmentBuilder

log = logging.getLogger(__name__)


def _lead_source(news: RankedNewsItem) -> SourceRef:
    return SourceRef(kind=news.source_kind, title=news.title, url=news.url)


class DefaultSummaryBranch:
    def __init__(self, *, segment_builder: SegmentBuilder, directory: CompanyDirectory,
                 confidence_scorer: ConfidenceScorer, composer: BannerComposer, publisher: Publisher,
                 store: BannerStore, summarizer: Summarizer, binder: EvidenceBinder,
                 content_scorer: ContentScorer, config: BranchConfig | None = None) -> None:
        self._segment_builder = segment_builder
        self._directory = directory
        self._confidence_scorer = confidence_scorer
        self._composer = composer
        self._publisher = publisher
        self._store = store
        self._summarizer = summarizer
        self._binder = binder
        self._content_scorer = content_scorer
        self._config = config or BranchConfig()

    # ------------------------------------------------------------------ banner
    def handle_banner(self, req: SummaryRequest) -> list[Banner]:
        companies = self._segment_companies(req.company)
        created: list[Banner] = []
        for cluster_news in self._top_clusters(req):
            cluster_req = req.model_copy(update={"news": cluster_news})
            cluster_id = cluster_news[0].cluster_id
            try:
                seg = self._segment_builder.build(cluster_req)
            except (SummarizationFailed, BudgetExhausted) as exc:
                log.warning("кластер пропущен: trace_id=%s cluster=%s reason=%s: %s",
                            req.trace_id, cluster_id, type(exc).__name__, exc)
                continue
            if seg is None:
                log.warning("кластер пропущен: trace_id=%s cluster=%s reason=пустое саммари", req.trace_id, cluster_id)
                continue
            for company in companies:
                banner = self._publish_for_company(req.trace_id, seg, company)
                if banner is not None:
                    created.append(banner)
        return created

    def _top_clusters(self, req: SummaryRequest) -> list[list[RankedNewsItem]]:
        by_cluster: dict[str, list[RankedNewsItem]] = {}
        for item in req.news:
            by_cluster.setdefault(item.cluster_id, []).append(item)
        ranked = sorted(by_cluster.values(), key=lambda items: max(n.rerank_score for n in items), reverse=True)
        return ranked[: self._config.max_clusters_per_request]

    def _segment_companies(self, representative: CompanyContext) -> list[CompanyContext]:
        companies = {representative.company_id: representative}
        for company in self._directory.active_in_segment(representative.segment_id):
            companies.setdefault(company.company_id, company)
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
        is_urgent = (is_new and banner.severity == Severity.ATTENTION and banner.decision == Decision.SHOW_SUMMARY
                     and confidence.score >= self._config.urgent_min_score)
        if is_urgent:
            self._publisher.push(company.company_id, [dto])
        return banner

    # ------------------------------------------------------------------ chat
    def handle_chat(self, req: SummaryRequest) -> ChatAnswer:
        lead = req.news[0]
        try:
            draft = self._summarizer.summarize(req)
        except (SummarizationFailed, BudgetExhausted) as exc:
            log.warning("чат: сырая новость: trace_id=%s reason=%s", req.trace_id, type(exc).__name__)
            return self._raw_news_answer(lead, confidence=0.0)

        claims, sources = self._binder.bind(draft, req)
        signals = self._content_scorer.score(req, draft, claims, sources)
        confidence = company_confidence(signals, lead_from_news(lead), draft.topic, req.company,
                                        has_sources=bool(sources))
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
