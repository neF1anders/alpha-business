"""Composition root: единственное место, где реализации связываются между собой."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .binder import DefaultEvidenceBinder
from .branch import DefaultSummaryBranch
from .composer import DefaultBannerComposer
from .config import BranchConfig
from .confidence import DefaultConfidenceScorer, DefaultContentScorer
from .interfaces import BannerStore, CompanyDirectory, LLMClient, SegmentCache
from .models import BannerDTO
from .publisher import DefaultPublisher, PushHandler
from .segment_builder import SegmentBuilder
from .stores import InMemoryBannerStore, InMemorySegmentCache
from .summarizer import LlmSummarizer


@dataclass(frozen=True)
class BranchRuntime:
    """Собранная ветка. `store` нужен бэку для обратной связи: mark_seen, suppress, invalidate_cluster."""
    branch: DefaultSummaryBranch
    store: BannerStore
    cache: SegmentCache
    config: BranchConfig

    def invalidate_cluster(self, cluster_id: str, reason: str) -> int:
        """Новое событие в кластере: сбрасывает и кэш саммари, и баннеры, чтобы следующий прогон пересобрал текст.

        Возвращает число снятых баннеров.
        """
        self.cache.invalidate_cluster(cluster_id)
        return self.store.invalidate_cluster(cluster_id, reason)

    def top_banners(self, company_id: str, now: Optional[datetime] = None) -> list[BannerDTO]:
        """Выдача для GET /signals: лучшие баннеры компании, не более config.top_k_serve."""
        return self.store.top_k(company_id, self.config.top_k_serve, now)


def build_branch(llm: LLMClient, directory: CompanyDirectory, config: BranchConfig | None = None,
                 push_handler: PushHandler | None = None, store: BannerStore | None = None,
                 cache: SegmentCache | None = None) -> BranchRuntime:
    """Без `store` и `cache` используются in-memory реализации (тесты и демо); в проде передаются Redis/Postgres."""
    config = config or BranchConfig()
    cache = cache if cache is not None else InMemorySegmentCache()
    store = store if store is not None else InMemoryBannerStore()
    summarizer = LlmSummarizer(llm, config)
    binder = DefaultEvidenceBinder(config)
    content_scorer = DefaultContentScorer()
    branch = DefaultSummaryBranch(
        segment_builder=SegmentBuilder(summarizer, binder, content_scorer, cache, config),
        directory=directory, confidence_scorer=DefaultConfidenceScorer(),
        composer=DefaultBannerComposer(config), publisher=DefaultPublisher(config, push_handler),
        store=store, summarizer=summarizer, binder=binder, content_scorer=content_scorer, config=config)
    return BranchRuntime(branch=branch, store=store, cache=cache, config=config)
