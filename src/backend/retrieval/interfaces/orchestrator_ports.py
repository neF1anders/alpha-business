"""Retrieval implementations of the orchestrator ports (NewsRetriever, EventRetriever, Reranker,
ProfileRepository), wired to the default PostgreSQL / local-model adapters.

Application use cases still exchange orchestrator domain models (NewsItem, CompanyProfile, ...)
directly; this module is the single place that binds them to concrete infrastructure.
"""
from ..application.event_context import EventRetrieval
from ..application.local_feed import ActivityProfiles as ActivityProfilesUseCase
from ..application.local_feed import LocalFeedRetriever as LocalFeedUseCase
from ..application.news_search import NewsSearch
from ..application.rerank import PairReranker
from ..infrastructure.cross_encoder import CrossEncoderScorer
from ..infrastructure.e5_encoder import LocalE5
from ..infrastructure.postgres_activity import PostgresActivityCatalog
from ..infrastructure.postgres_embeddings import PostgresEmbeddingStore
from ..infrastructure.postgres_news import PostgresNewsStore
from ..infrastructure.report_files import FilesystemReportCatalog


class PostgresNewsRetriever(NewsSearch):
    def __init__(self, *, timeout_s=0.65, lookback_days=90, max_concurrency=4, bank_enabled=True, reports_root=None):
        super().__init__(PostgresNewsStore(), FilesystemReportCatalog(), timeout_s=timeout_s,
                         lookback_days=lookback_days, max_concurrency=max_concurrency,
                         bank_enabled=bank_enabled, reports_root=reports_root)


class PostgresEventRetriever(EventRetrieval):
    def __init__(self, *, timeout_s=0.65, reports_root=None, max_concurrency=4):
        super().__init__(PostgresNewsStore(), FilesystemReportCatalog(), timeout_s=timeout_s,
                         reports_root=reports_root, max_concurrency=max_concurrency)


class ActivityProfiles(ActivityProfilesUseCase):
    def __init__(self, profiles):
        super().__init__(profiles, PostgresActivityCatalog())


def load_local_semantic():
    from ..application.semantic_index import SemanticNewsIndex
    return LocalE5(), SemanticNewsIndex(PostgresEmbeddingStore())


class LocalFeedRetriever(LocalFeedUseCase):
    def __init__(self, lexical=None, *, include_context=False):
        super().__init__(lexical or PostgresNewsRetriever(bank_enabled=False), load_local_semantic,
                         include_context=include_context)


class LocalPairReranker(PairReranker):
    def __init__(self, device='auto'):
        super().__init__(CrossEncoderScorer(device))

    def warmup(self):
        self.scorer.warmup()
