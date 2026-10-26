"""S1–S3c: ключ кластера -> кэш -> генерация с проверкой и повтором -> карта влияния -> кэш.

Содержимое кластера не зависит от компании, поэтому модель вызывается один раз на кластер для всех клиентов.
Параллельные запросы одного кластера (несколько сессий сразу) ждут одну генерацию, а не запускают свою.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from ..config import BranchConfig
from ..domain.models import ClaimType, ClusterSummary, ContentKey, SummaryRequest
from ..domain.ports import SummaryCache
from ..domain.scoring import lead_from_news
from .generator import ValidatedGenerator
from .impact import bind_impacts, normalized_countries, normalized_regions

log = logging.getLogger(__name__)


class ClusterSummaryBuilder:
    def __init__(self, generator: ValidatedGenerator, cache: SummaryCache, config: BranchConfig | None = None,
                 model_id: str = "llm-router") -> None:
        self._generator = generator
        self._cache = cache
        self._config = config or BranchConfig()
        self._model_id = model_id
        self._guard = threading.Lock()
        self._in_flight: dict[ContentKey, threading.Lock] = {}

    def key_for(self, cluster_id: str) -> ContentKey:
        return ContentKey(cluster_id=cluster_id, prompt_version=self._config.prompt_version)

    def cached(self, cluster_id: str) -> Optional[ClusterSummary]:
        return self._cache.get(self.key_for(cluster_id))

    def build(self, req: SummaryRequest) -> Optional[ClusterSummary]:
        """req.news — один кластер. SummarizationFailed и BudgetExhausted пробрасываются вызывающему коду."""
        req = req.model_copy(update={"news": sorted(req.news, key=lambda n: n.rerank_score, reverse=True)})
        key = self.key_for(req.news[0].cluster_id)
        cached = self._cache.get(key)
        if cached is not None:
            log.info("кэш кластера: попадание trace_id=%s key=%s", req.trace_id, key)
            return cached
        with self._lock_for(key):
            cached = self._cache.get(key)          # пока ждали, кластер мог собрать соседний запрос
            if cached is not None:
                log.info("кэш кластера: дождались соседний запрос trace_id=%s key=%s", req.trace_id, key)
                return cached
            log.info("кэш кластера: промах trace_id=%s key=%s", req.trace_id, key)
            return self._generate(req, key)

    def _lock_for(self, key: ContentKey) -> threading.Lock:
        with self._guard:
            return self._in_flight.setdefault(key, threading.Lock())

    def _generate(self, req: SummaryRequest, key: ContentKey) -> Optional[ClusterSummary]:
        content = self._generator.generate(req)
        draft, claims, sources, signals = content.draft, content.claims, content.sources, content.signals
        has_checked_claim = any(c.type in (ClaimType.FACT, ClaimType.BANK) for c in claims)
        if not has_checked_claim or not signals.structure_ok:
            log.warning("кластер отброшен: trace_id=%s key=%s fact_or_bank=%s structure_ok=%s",
                        req.trace_id, key, has_checked_claim, signals.structure_ok)
            return None
        summary = ClusterSummary(
            key=key, model_id=self._model_id, headline=draft.headline, for_you_template=draft.for_you_template,
            for_you_fallback=draft.for_you_fallback, topic=draft.topic, severity=draft.severity,
            claims=claims, sources=sources, lead=lead_from_news(req.news[0]), signals=signals,
            impacts=bind_impacts(draft, req), countries=normalized_countries(draft), regions=normalized_regions(draft))
        self._cache.put(summary, self._config.cache_ttl_s)
        return summary
