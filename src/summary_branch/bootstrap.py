"""Composition root: единственное место, где реализации связываются между собой."""
from __future__ import annotations

from dataclasses import dataclass

from .binder import DefaultEvidenceBinder
from .branch import DefaultSummaryBranch
from .composer import DefaultBannerComposer
from .config import BranchConfig
from .confidence import DefaultConfidenceScorer, DefaultContentScorer
from .interfaces import CompanyDirectory, LLMClient
from .publisher import DefaultPublisher, PushHandler
from .segment_builder import SegmentBuilder
from .stores import InMemoryBannerStore, InMemorySegmentCache
from .summarizer import LlmSummarizer


@dataclass(frozen=True)
class BranchRuntime:
    branch: DefaultSummaryBranch
    store: InMemoryBannerStore
    cache: InMemorySegmentCache


def build_branch(llm: LLMClient, directory: CompanyDirectory, config: BranchConfig | None = None,
                 push_handler: PushHandler | None = None) -> BranchRuntime:
    config = config or BranchConfig()
    summarizer = LlmSummarizer(llm, config)
    binder = DefaultEvidenceBinder(config)
    content_scorer = DefaultContentScorer()
    cache = InMemorySegmentCache()
    store = InMemoryBannerStore()
    branch = DefaultSummaryBranch(
        segment_builder=SegmentBuilder(summarizer, binder, content_scorer, cache, config),
        directory=directory, confidence_scorer=DefaultConfidenceScorer(),
        composer=DefaultBannerComposer(config), publisher=DefaultPublisher(config, push_handler),
        store=store, summarizer=summarizer, binder=binder, content_scorer=content_scorer, config=config)
    return BranchRuntime(branch=branch, store=store, cache=cache)
