"""Composition root: единственное место, где порты связываются с реализациями.

Запуск: python -m orchestrator (или uvicorn orchestrator.main:app --port 8000)
"""
from __future__ import annotations

import asyncio
from typing import Optional

from .api.app import Container, create_app
from .api.client import ClientServices
from .api.materials import register_materials
from .application.ports import (
    BannerFeed, CompanyDossierSource, EventRetriever, NewsRetriever, ProfileRepository, Reranker, SignalStream,
    SummaryBranch,
)
from .application.use_cases import AnswerChat, GetBanners, GetInlineSignal, GetSignals, Limits, SignalFeedback
from .config import Settings
from .domain.ranking import TagReranker
from .infrastructure.postgres_dossier import PostgresDossierSource
from .infrastructure.profiles_csv import CsvProfileRepository
from .infrastructure.signal_hub import SignalHub
from .infrastructure.summary_branch_adapter import BranchBannerFeed, SummaryBranchAdapter, banner_from_dto
from .infrastructure.template_branch import TemplateSummaryBranch
from retrieval.events import PostgresEventRetriever
from retrieval.news import PostgresNewsRetriever


def build_container(profiles: ProfileRepository, retriever: NewsRetriever, reranker: Reranker,
                    branch: SummaryBranch, limits: Limits, startup: tuple = (),
                    event_retriever: EventRetriever | None = None, *, dossiers: CompanyDossierSource | None = None,
                    feed: BannerFeed | None = None, stream: SignalStream | None = None,
                    demo_company_id: str = "") -> Container:
    pipeline = dict(event_retriever=event_retriever, dossiers=dossiers)
    get_banners = GetBanners(profiles, retriever, reranker, branch, limits, **pipeline)
    answer_chat = AnswerChat(profiles, retriever, reranker, branch, limits, feed=feed, **pipeline)
    client = None
    if feed is not None and stream is not None:
        client = ClientServices(get_signals=GetSignals(get_banners, feed, limits), inline=GetInlineSignal(feed, limits),
                                feedback=SignalFeedback(feed), answer_chat=answer_chat, stream=stream,
                                demo_company_id=demo_company_id)
    return Container(profiles=profiles, get_banners=get_banners, answer_chat=answer_chat, startup=startup,
                     client=client)


def build_summary_branch(settings: Settings, hub: SignalHub) -> tuple[SummaryBranch, Optional[BannerFeed]]:
    """Ветка саммари с LLM-шлюзом или детерминированной заглушкой; `template` — прежний шаблон без ветки."""
    mode = settings.resolved_llm_mode()
    if mode == "template":
        return TemplateSummaryBranch(), None
    from summary_branch import BranchConfig, build_branch
    from summary_branch.infrastructure.fakes import FakeLLMClient
    from summary_branch.infrastructure.llm_gateway import OpenAICompatibleLLMClient

    llm = OpenAICompatibleLLMClient.from_env() if mode == "gateway" else FakeLLMClient()
    runtime = build_branch(llm, BranchConfig(banner_store_path=settings.banner_store_path or None),
                           push_handler=lambda company_id, dtos: hub.publish_banners(
                               company_id, [banner_from_dto(d) for d in dtos]))
    return SummaryBranchAdapter(runtime), BranchBannerFeed(runtime)


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
    hub = SignalHub()
    branch, feed = build_summary_branch(settings, hub)
    container = build_container(profiles, retriever, reranker, branch, limits, startup=startup,
                                event_retriever=PostgresEventRetriever(reports_root=settings.reports_path),
                                dossiers=PostgresDossierSource(), feed=feed, stream=hub,
                                demo_company_id=settings.demo_company_id)
    app = create_app(container, settings.cors_origins)
    register_materials(app, settings.reports_path)
    return app


app = create_default_app()
