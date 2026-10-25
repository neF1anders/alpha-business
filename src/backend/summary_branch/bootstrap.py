"""Composition root: единственное место, где реализации связываются между собой."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .application.binder import DefaultEvidenceBinder
from .application.branch import DefaultSummaryBranch
from .application.composer import DefaultBannerComposer
from .config import BranchConfig
from .application.confidence import DefaultConfidenceScorer, DefaultContentScorer
from .application.generator import ValidatedGenerator
from .domain.ports import BannerStore, CompanyDirectory, LLMClient, SegmentCache
from .domain.models import BannerDTO
from .application.publisher import DefaultPublisher, PushHandler
from .application.segment_builder import SegmentBuilder
from .infrastructure.sqlite_store import SqliteBannerStore
from .infrastructure.stores import InMemoryBannerStore, InMemorySegmentCache
from .application.summarizer import LlmSummarizer


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
    """Без `store` и `cache` берутся локальные реализации: кэш в ОЗУ, баннеры в ОЗУ или, если задан
    `config.banner_store_path`, в файле SQLite (переживают перезапуск). Внешняя БД не нужна; свои реализации можно передать."""
    config = config or BranchConfig()
    cache = cache if cache is not None else InMemorySegmentCache()
    if store is None:
        store = SqliteBannerStore(config.banner_store_path) if config.banner_store_path else InMemoryBannerStore()
    summarizer = LlmSummarizer(llm, config)
    binder = DefaultEvidenceBinder(config)
    generator = ValidatedGenerator(summarizer, binder, DefaultContentScorer(), config)
    branch = DefaultSummaryBranch(
        segment_builder=SegmentBuilder(generator, cache, config),
        directory=directory, confidence_scorer=DefaultConfidenceScorer(),
        composer=DefaultBannerComposer(config), publisher=DefaultPublisher(config, push_handler),
        store=store, generator=generator, config=config)
    return BranchRuntime(branch=branch, store=store, cache=cache, config=config)
