"""Composition root: единственное место, где порты связываются с реализациями.

Запуск: python -m orchestrator (или uvicorn orchestrator.main:app --port 8000)
"""
from __future__ import annotations

from .api.app import Container, create_app
from .application.ports import NewsRetriever, ProfileRepository, Reranker, SummaryBranch
from .application.use_cases import AnswerChat, GetBanners, Limits
from .config import Settings
from .domain.ranking import TagReranker
from .infrastructure.profiles_csv import CsvProfileRepository
from .infrastructure.sample_news import InMemoryNewsRetriever
from .infrastructure.template_branch import TemplateSummaryBranch


def build_container(profiles: ProfileRepository, retriever: NewsRetriever, reranker: Reranker,
                    branch: SummaryBranch, limits: Limits, startup: tuple = ()) -> Container:
    return Container(
        profiles=profiles,
        get_banners=GetBanners(profiles, retriever, reranker, branch, limits),
        answer_chat=AnswerChat(profiles, retriever, reranker, branch, limits), startup=startup)


def create_default_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    limits = Limits(banner_slo_s=settings.banner_slo_s, chat_slo_s=settings.chat_slo_s)
    profiles = CsvProfileRepository(settings.tags_path)
    container = build_container(profiles, InMemoryNewsRetriever(), TagReranker(), TemplateSummaryBranch(), limits,
                                startup=(profiles.warmup,))
    return create_app(container)


app = create_default_app()
