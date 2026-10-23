"""S1–S3b: ключ контента -> кэш сегмента -> саммаризатор -> привязка доказательств -> сигналы -> кэш."""
from __future__ import annotations

import logging
from typing import Optional

from .config import BranchConfig
from .interfaces import ContentScorer, EvidenceBinder, SegmentCache, Summarizer
from .models import ClaimType, ContentKey, SegmentSummary, SummaryRequest
from .scoring import lead_from_news

log = logging.getLogger(__name__)


class SegmentBuilder:
    def __init__(self, summarizer: Summarizer, binder: EvidenceBinder, content_scorer: ContentScorer,
                 cache: SegmentCache, config: BranchConfig | None = None, model_id: str = "llm-router") -> None:
        self._summarizer = summarizer
        self._binder = binder
        self._content_scorer = content_scorer
        self._cache = cache
        self._config = config or BranchConfig()
        self._model_id = model_id

    def build(self, req: SummaryRequest) -> Optional[SegmentSummary]:
        """req.news — один кластер. SummarizationFailed и BudgetExhausted пробрасываются вызывающему коду."""
        req = req.model_copy(update={"news": sorted(req.news, key=lambda n: n.rerank_score, reverse=True)})
        lead = req.news[0]
        key = ContentKey(cluster_id=lead.cluster_id, segment_id=req.company.segment_id,
                         prompt_version=self._config.prompt_version)

        cached = self._cache.get(key)
        if cached is not None:
            log.info("кэш сегмента: попадание trace_id=%s key=%s", req.trace_id, key)
            return cached

        log.info("кэш сегмента: промах trace_id=%s key=%s", req.trace_id, key)
        draft = self._summarizer.summarize(req)
        claims, sources = self._binder.bind(draft, req)
        signals = self._content_scorer.score(req, draft, claims, sources)
        has_checked_claim = any(c.type in (ClaimType.FACT, ClaimType.BANK) for c in claims)
        if not has_checked_claim or not signals.structure_ok:
            log.warning("сегмент отброшен: trace_id=%s key=%s fact_or_bank=%s structure_ok=%s",
                        req.trace_id, key, has_checked_claim, signals.structure_ok)
            return None

        summary = SegmentSummary(
            key=key, model_id=self._model_id, headline=draft.headline, for_you_template=draft.for_you_template,
            for_you_fallback=draft.for_you_fallback, topic=draft.topic, severity=draft.severity,
            claims=claims, sources=sources, lead=lead_from_news(lead), signals=signals)
        self._cache.put(summary, self._config.cache_ttl_s)
        return summary
