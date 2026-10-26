"""Composition root: единственное место, где реализации связываются между собой."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .application.binder import DefaultEvidenceBinder
from .application.branch import DefaultSummaryBranch
from .application.cluster_summary import ClusterSummaryBuilder
from .application.composer import DefaultBannerComposer
from .application.confidence import DefaultConfidenceScorer, DefaultContentScorer
from .application.generator import ValidatedGenerator
from .application.personal import PersonalWriter
from .application.publisher import DefaultPublisher, PushHandler
from .application.summarizer import LlmSummarizer
from .config import BranchConfig
from .domain.models import BannerDTO
from .domain.ports import BannerStore, LLMClient, SummaryCache
from .infrastructure.sqlite_store import SqliteBannerStore
from .infrastructure.stores import InMemoryBannerStore, InMemorySummaryCache


@dataclass(frozen=True)
class BranchRuntime:
    """Собранная ветка. `store` нужен бэку для обратной связи: mark_seen, suppress, hide, counts."""
    branch: DefaultSummaryBranch
    store: BannerStore
    cache: SummaryCache
    config: BranchConfig

    def invalidate_cluster(self, cluster_id: str, reason: str) -> int:
        """Новое событие в кластере: сбрасывает и кэш саммари, и баннеры, чтобы следующий прогон пересобрал текст.

        Возвращает число снятых баннеров.
        """
        self.cache.invalidate_cluster(cluster_id)
        return self.store.invalidate_cluster(cluster_id, reason)

    def top_banners(self, company_id: str, now: Optional[datetime] = None) -> list[BannerDTO]:
        """Лучшие баннеры компании, не более config.top_k_serve."""
        return self.store.top_k(company_id, self.config.top_k_serve, now)


def build_branch(llm: LLMClient, config: BranchConfig | None = None, push_handler: PushHandler | None = None,
                 store: BannerStore | None = None, cache: SummaryCache | None = None) -> BranchRuntime:
    """Без `store` и `cache` берутся локальные реализации: кэш в ОЗУ, баннеры в ОЗУ или, если задан
    `config.banner_store_path`, в файле SQLite (переживают перезапуск). Внешняя БД не нужна; свои реализации можно передать."""
    config = config or BranchConfig()
    cache = cache if cache is not None else InMemorySummaryCache()
    if store is None:
        store = SqliteBannerStore(config.banner_store_path) if config.banner_store_path else InMemoryBannerStore()
    generator = ValidatedGenerator(LlmSummarizer(llm, config), DefaultEvidenceBinder(config), DefaultContentScorer(),
                                   config)
    branch = DefaultSummaryBranch(
        cluster_summary=ClusterSummaryBuilder(generator, cache, config),
        confidence_scorer=DefaultConfidenceScorer(), personal_writer=PersonalWriter(llm, config),
        composer=DefaultBannerComposer(config), publisher=DefaultPublisher(config, push_handler),
        store=store, generator=generator, config=config)
    return BranchRuntime(branch=branch, store=store, cache=cache, config=config)
