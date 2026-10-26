"""Сценарии оркестратора: баннеры сессии, выдача сигналов клиенту, обратная связь и чат.

Цепочка: профиль и досье компании -> retriever -> реранкер -> ветка саммари по каждой новости -> ответ.
Каждый внешний вызов ограничен по времени; при сбое или таймауте отдаётся деградированный ответ
(сама новость со ссылкой), а не ошибка. Баннер, не успевший к сроку, ветка доводит в фоне и присылает через
SignalSink: клиент меняет «только новость» на готовый баннер без перезагрузки.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Awaitable, Callable, Optional, TypeVar

from ..domain.errors import CompanyNotFound, DependencyUnavailable
from ..domain.models import (
    Banner, BankMaterial, BudgetTier, ChatAnswer, ChatTurn, Claim, CompanyDossier, CompanyProfile, Mode, NewsQuery,
    RankedNews, Severity, SourceRef, SummaryRequest, TraceStep, audience_id, follow_up_search_text, raw_banner_id,
    recent_history, utcnow,
)
from ..domain.profile_rules import counterparty_search_tags
from .ports import (
    BannerFeed, CompanyDossierSource, EventRetriever, NewsRetriever, ProfileRepository, Reranker, SummaryBranch,
)
from .trace import Deadline, Trace

T = TypeVar("T")

MATCH_NAMESPACES = ("factor", "sector", "okved", "country", "region")
INLINE_SEVERITIES = (Severity.ATTENTION, Severity.OPPORTUNITY)


@dataclass(frozen=True)
class Limits:
    banner_slo_s: float = 2.0
    chat_slo_s: float = 6.0
    retrieve_limit: int = 20
    banner_top_k: int = 3
    chat_top_k: int = 5
    retrieve_share: float = 0.4                      # какую долю остатка времени можно отдать retriever
    dossier_share: float = 0.3                       # досье читается параллельно с ретривом
    lite_below_s: float = 3.0                        # в чате остаток меньше -> бюджет lite (короткий ответ, без повтора)
    fallback_banner_ttl: timedelta = timedelta(hours=24)
    signals_limit: int = 20                          # сколько сохранённых баннеров отдаётся в панель


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
    banner_id: Optional[str] = None                  # вопрос задан из баннера: его тема входит в поиск
    history: tuple[ChatTurn, ...] = ()               # предыдущие реплики диалога, старые первыми


@dataclass(frozen=True)
class BannersResult:
    trace_id: str
    banners: tuple[Banner, ...]
    degraded: tuple[str, ...] = ()
    steps: tuple[TraceStep, ...] = ()


@dataclass(frozen=True)
class SignalsResult:
    trace_id: str
    items: tuple[Banner, ...]
    hidden: int = 0
    degraded: tuple[str, ...] = ()

    def counts(self) -> dict[str, int]:
        counts = {severity.value: 0 for severity in Severity}
        for banner in self.items:
            counts[banner.severity.value] += 1
        return {**counts, "hidden": self.hidden}


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
    dossier: Optional[CompanyDossier] = None


class _Pipeline:
    """Общие шаги обоих сценариев."""

    def __init__(self, profiles: ProfileRepository, retriever: NewsRetriever, reranker: Reranker,
                 branch: SummaryBranch, limits: Limits, *, event_retriever: Optional[EventRetriever] = None,
                 dossiers: Optional[CompanyDossierSource] = None) -> None:
        self._profiles, self._retriever, self._reranker = profiles, retriever, reranker
        self._branch, self._limits = branch, limits
        self._event_retriever = event_retriever
        self._dossiers = dossiers

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

    async def _dossier(self, profile: CompanyProfile, trace: Trace, deadline: Deadline) -> Optional[CompanyDossier]:
        """Без досье персонализация опирается только на теги: это деградация, а не ошибка."""
        if self._dossiers is None:
            return None
        with trace.step("dossier") as info:
            try:
                dossier = await self._call("dossier", self._dossiers.get(profile.company_id),
                                           deadline.slice(self._limits.dossier_share))
            except DependencyUnavailable as exc:
                trace.degrade(str(exc))
                info["unavailable"] = True
                return None
            if dossier is not None:
                info.update(suppliers=len(dossier.suppliers), buyers=len(dossier.buyers),
                            affiliates=len(dossier.affiliate_sectors),
                            search_tags=sorted(counterparty_search_tags(dossier) - profile.tags))
            return dossier

    async def _find_with_dossier(self, profile: CompanyProfile, question: Optional[str], top_k: int, trace: Trace,
                                 deadline: Deadline, search_text: Optional[str] = None) -> _Found:
        """Досье читается до поиска: отрасли ключевых контрагентов расширяют поиск новостями о нишах партнёров.

        Сами теги компании не меняются: расширенный набор живёт только в запросе к retriever и реранкеру.
        """
        dossier = await self._dossier(profile, trace, deadline)
        extra = counterparty_search_tags(dossier) - profile.tags if dossier else frozenset()
        searched = replace(profile, tags=profile.tags | extra) if extra else profile
        found = await self._find(searched, question, top_k, trace, deadline, search_text)
        found.profile, found.dossier = profile, dossier
        return found

    async def _find(self, profile: CompanyProfile, question: Optional[str], top_k: int,
                    trace: Trace, deadline: Deadline, search_text: Optional[str] = None) -> _Found:
        chat = question is not None
        query = NewsQuery(tags=profile.tags_in(*MATCH_NAMESPACES), question=search_text or question,
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
                 budget: BudgetTier, history: tuple[ChatTurn, ...] = ()) -> SummaryRequest:
        return SummaryRequest(trace_id=trace.trace_id, mode=mode, company=found.profile, news=tuple(found.ranked),
                              bank_materials=found.bank, question=question, budget=budget, dossier=found.dossier,
                              history=history)


class GetBanners(_Pipeline):
    """Сигнал «сессия началась»: по баннеру на каждую отобранную новость в пределах SLO.

    Ветка вызывается по каждой новости параллельно. Новость, не успевшая к сроку, приходит как «только новость»
    с id `raw-<cluster>`, а готовый баннер ветка присылает позже через SignalSink.
    """

    async def execute(self, cmd: BannersCommand,
                      on_step: Optional[Callable[[TraceStep], None]] = None) -> BannersResult:
        trace = Trace(cmd.trace_id or uuid.uuid4().hex, on_step)
        deadline = Deadline(self._limits.banner_slo_s)
        profile = await self._profile(cmd.company_id, cmd.role, trace)
        try:
            found = await self._find_with_dossier(profile, None, self._limits.banner_top_k, trace, deadline)
        except DependencyUnavailable as exc:
            trace.degrade(str(exc))
            return self._result(trace, ())
        if not found.ranked:
            trace.degrade("нет подходящих новостей")
            return self._result(trace, ())

        req = self._request(trace, Mode.BANNER, found, None, BudgetTier.FULL)
        with trace.step("summary_branch", news=len(found.ranked)) as info:
            outcomes = await asyncio.gather(*(self._banner_for(req, ranked, deadline) for ranked in found.ranked))
            late = [banner for banner, failure in outcomes if failure]
            for reason in dict.fromkeys(failure for _, failure in outcomes if failure):
                trace.degrade(reason)
            banners = [banner for banner, _ in outcomes if banner is not None]
            info.update(banners=len(banners), raw_fallback=len(late))
        return self._result(trace, tuple(banners))

    async def _banner_for(self, req: SummaryRequest, ranked: RankedNews,
                          deadline: Deadline) -> tuple[Optional[Banner], str]:
        """(баннер или None, причина деградации). Ошибка и таймаут ветки дают баннер «только новость»."""
        news_id = ranked.item.news_id
        bank = tuple(m for m in req.bank_materials if not m.news_ids or news_id in m.news_ids)
        one = replace(req, news=(ranked,), bank_materials=bank)
        try:
            return await self._call("summary_branch", self._branch.banner(one), deadline.remaining()), ""
        except DependencyUnavailable as exc:
            return self._raw_banner(ranked), str(exc)

    def _raw_banner(self, ranked: RankedNews) -> Banner:
        """Деградация без LLM: заголовок новости, ссылка и источник."""
        item = ranked.item
        return Banner(
            banner_id=raw_banner_id(item.cluster_id), severity=Severity.WATCH, label="Новость", headline=item.title,
            for_you="", sources=(SourceRef(item.source_kind, item.title, item.url, item.news_id),),
            expires_at=utcnow() + self._limits.fallback_banner_ttl, raw_news=True, cluster_id=item.cluster_id)

    @staticmethod
    def _result(trace: Trace, banners: tuple[Banner, ...]) -> BannersResult:
        return BannersResult(trace.trace_id, banners, tuple(trace.degraded), tuple(trace.steps))


class GetSignals:
    """Панель ассистента: баннеры этой сессии и сохранённые баннеры прошлых сессий, счётчики по всем актуальным."""

    def __init__(self, get_banners: GetBanners, feed: BannerFeed, limits: Limits) -> None:
        self._get_banners, self._feed, self._limits = get_banners, feed, limits

    async def execute(self, cmd: BannersCommand) -> SignalsResult:
        session = await self._get_banners.execute(cmd)
        audience = audience_id(cmd.company_id, cmd.role)
        stored = await self._feed.live(audience, self._limits.signals_limit)
        ready_clusters = {b.cluster_id for b in (*session.banners, *stored) if not b.raw_news and b.cluster_id}
        items: dict[str, Banner] = {}
        for banner in (*session.banners, *stored):
            if banner.raw_news and banner.cluster_id in ready_clusters:
                continue                                 # готовый баннер кластера уже есть: «только новость» не нужна
            items.setdefault(banner.banner_id, banner)
        hidden = await self._feed.hidden_count(audience)
        return SignalsResult(session.trace_id, tuple(items.values())[: self._limits.signals_limit], hidden,
                             session.degraded)


class GetInlineSignal:
    """Плашка на странице действия: один сильный баннер, путь влияния которого относится к этой странице."""

    def __init__(self, feed: BannerFeed, limits: Limits) -> None:
        self._feed, self._limits = feed, limits

    async def execute(self, audience: str, page: str) -> Optional[Banner]:
        for banner in await self._feed.live(audience, self._limits.signals_limit):
            if (page in banner.placements and banner.severity in INLINE_SEVERITIES
                    and not banner.caveat and not banner.raw_news):
                return banner
        return None


class SignalFeedback:
    """Реакции клиента на баннер: просмотр, «не про меня», «скрыть».

    `audience` — ключ выдачи (audience_id): реакции относятся к паре компания × роль.
    """

    def __init__(self, feed: BannerFeed) -> None:
        self._feed = feed

    async def seen(self, audience: str, banner_id: str) -> None:
        await self._feed.mark_seen(audience, banner_id)

    async def not_relevant(self, audience: str, banner_id: str, reason: str) -> None:
        await self._feed.suppress(audience, banner_id, reason)

    async def dismiss(self, audience: str, banner_id: str) -> None:
        await self._feed.hide(audience, banner_id)


class AnswerChat(_Pipeline):
    """Вопрос в чате: всегда генерация, ответ за ~6 с."""

    def __init__(self, *args, feed: Optional[BannerFeed] = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._feed = feed

    async def execute(self, cmd: ChatCommand,
                      on_step: Optional[Callable[[TraceStep], None]] = None) -> ChatResult:
        trace = Trace(cmd.trace_id or uuid.uuid4().hex, on_step)
        deadline = Deadline(self._limits.chat_slo_s)
        profile = await self._profile(cmd.company_id, cmd.role, trace)
        history = recent_history(cmd.history)
        search_text = await self._search_text(cmd) or follow_up_search_text(cmd.question, history)
        found = await self._find_with_dossier(profile, cmd.question, self._limits.chat_top_k, trace, deadline,
                                              search_text)
        if not found.ranked:
            trace.degrade("нет подходящих материалов")
            return self._result(trace, self._empty_answer())

        budget = BudgetTier.LITE if deadline.remaining() < self._limits.lite_below_s else BudgetTier.FULL
        req = self._request(trace, Mode.CHAT, found, cmd.question, budget, history)
        with trace.step("summary_branch", budget=budget.value) as info:
            try:
                answer = await self._call("summary_branch", self._branch.chat(req), deadline.remaining())
            except DependencyUnavailable as exc:
                trace.degrade(str(exc))
                answer = self._raw_answer(found.ranked[0])
                info["fallback"] = "raw_news"
            info.update(raw_news=answer.raw_news, confidence=answer.confidence)
        return self._result(trace, answer)

    async def _search_text(self, cmd: ChatCommand) -> Optional[str]:
        """Вопрос из баннера («Подробнее») ищется по заголовку его новости: «что это значит для меня» сам ничего не найдёт,
        а личный заголовок баннера и слова вопроса только мешают полнотекстовому поиску."""
        if not cmd.banner_id or self._feed is None:
            return None
        banner = await self._feed.get(audience_id(cmd.company_id, cmd.role), cmd.banner_id)
        if banner is None:
            return None
        return banner.sources[0].title if banner.sources else banner.headline

    @staticmethod
    def _raw_answer(top: RankedNews) -> ChatAnswer:
        item = top.item
        return ChatAnswer(
            answer_id=f"raw-{item.news_id}", claims=(Claim("fact", item.title, (item.url,)),),
            sources=(SourceRef(item.source_kind, item.title, item.url, item.news_id),), confidence=0.0, raw_news=True)

    @staticmethod
    def _empty_answer() -> ChatAnswer:
        return ChatAnswer(answer_id=f"empty-{uuid.uuid4().hex[:8]}", claims=(), sources=(), confidence=0.0,
                          raw_news=True)

    @staticmethod
    def _result(trace: Trace, answer: ChatAnswer) -> ChatResult:
        return ChatResult(trace.trace_id, answer, tuple(trace.degraded), tuple(trace.steps))
