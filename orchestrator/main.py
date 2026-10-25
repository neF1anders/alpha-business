"""Composition root: единственное место, где порты связываются с реализациями.

Запуск: python -m orchestrator (или uvicorn orchestrator.main:app --port 8000)
"""
from __future__ import annotations

import asyncio

from .api.app import Container, create_app
from .application.ports import EventRetriever, NewsRetriever, ProfileRepository, Reranker, SummaryBranch
from .application.use_cases import AnswerChat, GetBanners, Limits
from .config import Settings
from .domain.ranking import TagReranker
from .infrastructure.profiles_csv import CsvProfileRepository
from retrieval.news import PostgresNewsRetriever
from retrieval.events import PostgresEventRetriever
from .api.materials import register_materials
from .infrastructure.template_branch import TemplateSummaryBranch


def build_container(profiles: ProfileRepository, retriever: NewsRetriever, reranker: Reranker,
                    branch: SummaryBranch, limits: Limits, startup: tuple = (),
                    event_retriever: EventRetriever | None = None) -> Container:
    return Container(
        profiles=profiles,
        get_banners=GetBanners(profiles, retriever, reranker, branch, limits, event_retriever=event_retriever),
        answer_chat=AnswerChat(profiles, retriever, reranker, branch, limits, event_retriever=event_retriever), startup=startup)


def create_default_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    limits = Limits(banner_slo_s=settings.banner_slo_s, chat_slo_s=settings.chat_slo_s)
    profiles = CsvProfileRepository(settings.tags_path)
    retriever = PostgresNewsRetriever(bank_enabled=False)
    reranker = TagReranker()
    startup = (profiles.warmup,)
    if settings.news_search_mode == "local_semantic":
        from retrieval.feed import ActivityProfiles, LocalFeedRetriever
        from retrieval.reranker import LocalPairReranker
        profiles = ActivityProfiles(profiles)
        retriever = LocalFeedRetriever(retriever)
        reranker = LocalPairReranker()
        limits = Limits(banner_slo_s=settings.banner_slo_s, chat_slo_s=settings.chat_slo_s,
                        retrieve_limit=settings.news_candidate_limit)
        async def warm_models():
            await retriever.warmup()
            await asyncio.to_thread(reranker.warmup)
        startup = (profiles.warmup, warm_models)
    elif settings.news_search_mode != "lexical":
        raise ValueError("NEWS_SEARCH_MODE must be lexical or local_semantic")
    container = build_container(profiles, retriever, reranker, TemplateSummaryBranch(), limits,
                                startup=startup,
                                event_retriever=PostgresEventRetriever(reports_root=settings.reports_path))
    app = create_app(container)
    register_materials(app, settings.reports_path)
    return app


app = create_default_app()
