"""Сценарии оркестратора: баннеры и чат.

Цепочка: профиль -> retriever -> реранкер -> ветка саммари (кэш, генерация, валидация) -> ответ.
Каждый внешний вызов ограничен по времени; при сбое или таймауте отдаётся деградированный ответ
(сама новость со ссылкой), а не ошибка.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Awaitable, Callable, Optional, TypeVar

from ..domain.errors import CompanyNotFound, DependencyUnavailable
from ..domain.models import (
    Banner, BankMaterial, BudgetTier, ChatAnswer, Claim, CompanyProfile, Mode, NewsQuery, RankedNews,
    Severity, SourceRef, SummaryRequest, TraceStep, utcnow,
)
from .ports import EventRetriever, NewsRetriever, ProfileRepository, Reranker, SummaryBranch
from .trace import Deadline, Trace

T = TypeVar("T")

MATCH_NAMESPACES = ("factor", "sector", "okved", "country", "region")


@dataclass(frozen=True)
class Limits:
    banner_slo_s: float = 2.0
    chat_slo_s: float = 6.0
    retrieve_limit: int = 20
    banner_top_k: int = 3
    chat_top_k: int = 5
    retrieve_share: float = 0.4                      # какую долю остатка времени можно отдать retriever
    lite_below_s: float = 3.0                        # в чате остаток меньше -> бюджет lite (короткий ответ, без повтора)
    fallback_banner_ttl: timedelta = timedelta(hours=24)


@dataclass(frozen=True)
class BannersCommand:
    company_id: str
    role: Optional[str] = None
    trace_id: Optional[str] = None


@dataclass(frozen=True)
class ChatCommand:
    company_id: str
    question: str
    role: Optional[str] = None
    trace_id: Optional[str] = None


@dataclass(frozen=True)
class BannersResult:
    trace_id: str
    banners: tuple[Banner, ...]
    degraded: tuple[str, ...] = ()
    steps: tuple[TraceStep, ...] = ()


@dataclass(frozen=True)
class ChatResult:
    trace_id: str
    answer: ChatAnswer
    degraded: tuple[str, ...] = ()
    steps: tuple[TraceStep, ...] = ()


@dataclass
class _Found:
    profile: CompanyProfile
    ranked: list[RankedNews] = field(default_factory=list)
    bank: tuple[BankMaterial, ...] = ()


class _Pipeline:
    """Общие шаги обоих сценариев."""

    def __init__(self, profiles: ProfileRepository, retriever: NewsRetriever, reranker: Reranker,
                 branch: SummaryBranch, limits: Limits, *, event_retriever: Optional[EventRetriever] = None) -> None:
        self._profiles, self._retriever, self._reranker = profiles, retriever, reranker
        self._branch, self._limits = branch, limits
        self._event_retriever = event_retriever

    async def _call(self, name: str, coro: Awaitable[T], timeout: float) -> T:
        """Таймаут и сбои внешнего компонента превращаются в DependencyUnavailable."""
        try:
            return await asyncio.wait_for(coro, timeout=max(timeout, 0.001))
        except asyncio.TimeoutError as exc:
            raise DependencyUnavailable(name, f"таймаут {timeout:.2f} с") from exc
        except DependencyUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - любой сбой адаптера не должен ронять запрос
            raise DependencyUnavailable(name, "сервис недоступен") from None

    async def _profile(self, company_id: str, role: Optional[str], trace: Trace) -> CompanyProfile:
        with trace.step("profile", company_id=company_id) as info:
            profile = await self._profiles.get(company_id)
            if profile is None:
                raise CompanyNotFound(company_id)
            if role:
                profile = profile.with_role(role)
            info.update(segment=profile.segment_id, tags=len(profile.tags), activity=profile.activity)
            return profile

    async def _find(self, profile: CompanyProfile, question: Optional[str], top_k: int,
                    trace: Trace, deadline: Deadline) -> _Found:
        chat = question is not None
        query = NewsQuery(tags=profile.tags_in(*MATCH_NAMESPACES), question=question,
                          limit=self._limits.retrieve_limit, company=profile)
        with trace.step("retrieve", chat=chat) as info:
            result = await self._call("retriever", self._retriever.retrieve(query),
                                      deadline.slice(self._limits.retrieve_share))
            info["found"] = len(result.news)
            info["bank_materials"] = len(result.bank_materials)
        with trace.step("rerank") as info:
            ranked = await self._call("reranker", asyncio.to_thread(
                self._reranker.rerank, profile, list(result.news), top_k, require_tag_match=not chat),
                deadline.remaining())
            info.update(kept=len(ranked), top=[(r.item.news_id, r.score, list(r.matched_tags)) for r in ranked[:3]],
                        ranking_reasons={r.item.news_id: list(r.reasons) for r in ranked[:3]})
        bank = result.bank_materials
        if self._event_retriever is not None and ranked:
            with trace.step("event_context") as info:
                try:
                    contexts = await self._call("event_context", self._contexts(ranked, profile),
                                                deadline.slice(0.5))
                    merged = {}
                    for context in contexts:
                        for material in context.bank_materials:
                            previous = merged.get(material.doc_id)
                            merged[material.doc_id] = material if previous is None else replace(previous,
                                news_ids=tuple(sorted(set(previous.news_ids) | set(material.news_ids))))
                    bank = tuple(merged.values())
                    info.update(events=[dict(news_id=c.news_id,status=c.status,topics=c.topics,
                                             matched_tags=c.matched_company_tags,trace=c.trace) for c in contexts],
                                bank_materials=len(bank))
                except DependencyUnavailable as exc:
                    # Keep already selected news; never substitute unrelated evidence.
                    bank = ()
                    info.update(bank_materials=0,unavailable=True)
                    trace.degrade(str(exc))
        return _Found(profile, ranked, bank)

    async def _contexts(self, ranked, profile):
        # Cancel siblings on failure; do not leave database requests in background.
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(self._event_retriever.retrieve(r.item, profile, limit=2))
                     for r in ranked]
        return [task.result() for task in tasks]

    def _request(self, trace: Trace, mode: Mode, found: _Found, question: Optional[str],
                 budget: BudgetTier) -> SummaryRequest:
        return SummaryRequest(trace_id=trace.trace_id, mode=mode, company=found.profile, news=tuple(found.ranked),
                              bank_materials=found.bank, question=question, budget=budget)


class GetBanners(_Pipeline):
    """Сигнал «сессия началась» или открытие главной: самые релевантные баннеры компании за ~2 с."""

    async def execute(self, cmd: BannersCommand,
                      on_step: Optional[Callable[[TraceStep], None]] = None) -> BannersResult:
        trace = Trace(cmd.trace_id or uuid.uuid4().hex, on_step)
        deadline = Deadline(self._limits.banner_slo_s)
        profile = await self._profile(cmd.company_id, cmd.role, trace)
        try:
            found = await self._find(profile, None, self._limits.banner_top_k, trace, deadline)
        except DependencyUnavailable as exc:
            trace.degrade(str(exc))
            return self._result(trace, ())
        if not found.ranked:
            trace.degrade("нет подходящих новостей")
            return self._result(trace, ())

        req = self._request(trace, Mode.BANNER, found, None, BudgetTier.FULL)
        with trace.step("summary_branch", clusters=len(found.ranked)) as info:
            try:
                banners = await self._call("summary_branch", self._branch.banners(req), deadline.remaining())
            except DependencyUnavailable as exc:
                trace.degrade(str(exc))
                banners = self._raw_banners(found.ranked)
                info["fallback"] = "raw_news"
            info["banners"] = len(banners)
        return self._result(trace, tuple(banners))

    def _raw_banners(self, ranked: list[RankedNews]) -> list[Banner]:
        """Деградация без LLM: заголовок новости, ссылка и источник."""
        expires = utcnow() + self._limits.fallback_banner_ttl
        return [Banner(
            banner_id=f"raw-{r.item.news_id}", severity=Severity.WATCH, label="Новость", headline=r.item.title,
            for_you="", sources=(SourceRef(r.item.source_kind, r.item.title, r.item.url),),
            expires_at=expires, raw_news=True) for r in ranked]

    @staticmethod
    def _result(trace: Trace, banners: tuple[Banner, ...]) -> BannersResult:
        return BannersResult(trace.trace_id, banners, tuple(trace.degraded), tuple(trace.steps))


class AnswerChat(_Pipeline):
    """Вопрос в чате: всегда генерация, ответ за ~6 с."""

    async def execute(self, cmd: ChatCommand,
                      on_step: Optional[Callable[[TraceStep], None]] = None) -> ChatResult:
        trace = Trace(cmd.trace_id or uuid.uuid4().hex, on_step)
        deadline = Deadline(self._limits.chat_slo_s)
        profile = await self._profile(cmd.company_id, cmd.role, trace)
        found = await self._find(profile, cmd.question, self._limits.chat_top_k, trace, deadline)
        if not found.ranked:
            trace.degrade("нет подходящих материалов")
            return self._result(trace, self._empty_answer())

        budget = BudgetTier.LITE if deadline.remaining() < self._limits.lite_below_s else BudgetTier.FULL
        req = self._request(trace, Mode.CHAT, found, cmd.question, budget)
        with trace.step("summary_branch", budget=budget.value) as info:
            try:
                answer = await self._call("summary_branch", self._branch.chat(req), deadline.remaining())
            except DependencyUnavailable as exc:
                trace.degrade(str(exc))
                answer = self._raw_answer(found.ranked[0])
                info["fallback"] = "raw_news"
            info.update(raw_news=answer.raw_news, confidence=answer.confidence)
        return self._result(trace, answer)

    @staticmethod
    def _raw_answer(top: RankedNews) -> ChatAnswer:
        item = top.item
        return ChatAnswer(
            answer_id=f"raw-{item.news_id}", claims=(Claim("fact", item.title, (item.url,)),),
            sources=(SourceRef(item.source_kind, item.title, item.url),), confidence=0.0, raw_news=True)

    @staticmethod
    def _empty_answer() -> ChatAnswer:
        return ChatAnswer(answer_id=f"empty-{uuid.uuid4().hex[:8]}", claims=(), sources=(), confidence=0.0,
                          raw_news=True)

    @staticmethod
    def _result(trace: Trace, answer: ChatAnswer) -> ChatResult:
        return ChatResult(trace.trace_id, answer, tuple(trace.degraded), tuple(trace.steps))
