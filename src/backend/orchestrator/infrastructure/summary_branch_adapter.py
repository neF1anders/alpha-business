"""Адаптер ветки саммари (`summary_branch`) к портам SummaryBranch и BannerFeed.

Переводит модели оркестратора в модели ветки и обратно, словарь тегов оркестратора (docs/tags.md) — в темы и
разделы ОКВЭД, с которыми работает ветка. Ветка синхронная и тратит время на LLM, поэтому вызывается в потоках.
"""
from __future__ import annotations

import asyncio
from datetime import timezone
from typing import Optional

from summary_branch import BranchRuntime
from summary_branch.domain import models as branch

from ..domain.models import (
    Banner, ChatAnswer, Claim, CompanyDossier, CompanyProfile, CounterpartySector, Evidence, Severity, SourceKind,
    SourceRef, SummaryRequest,
)
from ..domain.profile_rules import SECTOR_RANGES

# Тег оркестратора -> тема ветки: по теме ветка понимает, к чему чувствителен профиль компании
_FACTOR_TOPICS = {"factor:key_rate": "rate", "factor:fx": "fx", "factor:labor": "labor",
                  "factor:consumer_demand": "demand"}
_FACTOR_DIVISIONS = {"factor:construction_market": ("41", "42", "43", "68")}
_PRODUCTS = {"rko": branch.Product.RKO, "guarantee": branch.Product.GUARANTEE, "credit": branch.Product.LOAN,
             "loan": branch.Product.LOAN, "leasing": branch.Product.LEASING, "deposit": branch.Product.DEPOSIT,
             "payroll": branch.Product.PAYROLL, "factoring": branch.Product.FACTORING}
_SECTOR_DIVISIONS = {name: tuple(f"{code:02d}" for code in range(low, high + 1)) for low, high, name in SECTOR_RANGES}


class SummaryBranchAdapter:
    """Реализует SummaryBranch поверх собранной ветки."""

    def __init__(self, runtime: BranchRuntime) -> None:
        self._runtime = runtime

    async def banner(self, req: SummaryRequest) -> Optional[Banner]:
        dto = await asyncio.to_thread(self._runtime.branch.banner, to_branch_request(req))
        return banner_from_dto(dto) if dto is not None else None

    async def chat(self, req: SummaryRequest) -> ChatAnswer:
        answer = await asyncio.to_thread(self._runtime.branch.handle_chat, to_branch_request(req))
        return answer_from_branch(answer)


class BranchBannerFeed:
    """Реализует BannerFeed поверх хранилища баннеров ветки (SQLite или ОЗУ)."""

    def __init__(self, runtime: BranchRuntime) -> None:
        self._store = runtime.store

    async def live(self, company_id: str, limit: int) -> list[Banner]:
        return [banner_from_dto(d) for d in await asyncio.to_thread(self._store.top_k, company_id, limit)]

    async def get(self, company_id: str, banner_id: str) -> Optional[Banner]:
        dto = await asyncio.to_thread(self._store.get, company_id, banner_id)
        return banner_from_dto(dto) if dto is not None else None

    async def hidden_count(self, company_id: str) -> int:
        return (await asyncio.to_thread(self._store.counts, company_id)).get("hidden", 0)

    async def mark_seen(self, company_id: str, banner_id: str) -> None:
        await asyncio.to_thread(self._store.mark_seen, company_id, banner_id)

    async def suppress(self, company_id: str, banner_id: str, reason: str) -> None:
        await asyncio.to_thread(self._store.suppress, company_id, banner_id, reason)

    async def hide(self, company_id: str, banner_id: str) -> None:
        await asyncio.to_thread(self._store.hide, company_id, banner_id)


# --------------------------------------------------------------------------- оркестратор -> ветка
def to_branch_request(req: SummaryRequest) -> branch.SummaryRequest:
    return branch.SummaryRequest(
        trace_id=req.trace_id, mode=branch.Mode(req.mode.value), question=req.question,
        company=to_company_context(req.company, req.dossier),
        news=[_news(r) for r in req.news],
        bank_materials=[branch.BankMaterialRef(doc_id=m.doc_id, title=m.title, page=m.page, claim_text=m.claim_text,
                                               url=m.url) for m in req.bank_materials],
        budget=branch.BudgetTier(req.budget.value),
        history=[branch.DialogueTurn(role=t.role, text=t.text) for t in req.history])


def to_company_context(profile: CompanyProfile, dossier: Optional[CompanyDossier]) -> branch.CompanyContext:
    return branch.CompanyContext(
        # ветка хранит и рассылает баннеры по company_id: ключ выдачи разводит роли одной компании
        company_id=profile.audience_id, segment_id=profile.segment_id, role=profile.role,
        tags=sorted(profile.tags), features=list(profile.features),
        profile=_profile_from_dossier(dossier) if dossier else _profile_from_tags(profile))


def news_tags(tags: frozenset[str]) -> list[str]:
    """Теги новости в словаре ветки: исходные плюс темы (`rate`) и разделы ОКВЭД (`okved:46`) по отраслевым тегам."""
    out = set(tags)
    for tag in tags:
        if tag in _FACTOR_TOPICS:
            out.add(_FACTOR_TOPICS[tag])
        for division in _FACTOR_DIVISIONS.get(tag, ()):
            out.add(f"okved:{division}")
        if tag.startswith("sector:"):
            out.update(f"okved:{d}" for d in _SECTOR_DIVISIONS.get(tag.split(":", 1)[1], ()))
    return sorted(out)


def _news(ranked) -> branch.RankedNewsItem:
    item = ranked.item
    published = item.published_at if item.published_at.tzinfo else item.published_at.replace(tzinfo=timezone.utc)
    return branch.RankedNewsItem(
        news_id=item.news_id, cluster_id=item.cluster_id, title=item.title, text=item.text,
        source_kind=branch.SourceKind(item.source_kind.value), url=item.url, primary_url=item.primary_url,
        published_at=published, tags=news_tags(item.tags), rerank_score=min(max(ranked.score, 0.0), 1.0))


def _profile_from_dossier(d: CompanyDossier) -> branch.CompanyProfile:
    return branch.CompanyProfile(
        okved_code=d.okved_code, okved_name=d.okved_name, age_band=d.age_band, lifecycle=d.lifecycle,
        workers_band=d.workers_band, revenue=d.revenue, region=d.region, city=d.city,
        relationship=_relationship(d.client_type), products={_PRODUCTS[p] for p in d.products if p in _PRODUCTS},
        foreign_trade=d.foreign_trade, trade_countries=list(d.trade_countries), negative_flag=d.negative_flag,
        group_id=d.group_id, affiliate_sectors=list(d.affiliate_sectors),
        payments=branch.PaymentProfile(
            counterparty_sectors=_combined_shares(d), suppliers=[_side(s) for s in d.suppliers],
            buyers=[_side(s) for s in d.buyers], top_supplier_share=d.top_supplier_share,
            top_buyer_share=d.top_buyer_share))


def _profile_from_tags(profile: CompanyProfile) -> Optional[branch.CompanyProfile]:
    """Без досье профиль собирается из тегов: отрасль, регион, страны ВЭД, продукты, штат, тип клиента."""
    values: dict[str, list[str]] = {}
    for tag in profile.tags:
        namespace, _, value = tag.partition(":")
        values.setdefault(namespace, []).append(value)
    if "okved" not in values:
        return None
    countries = sorted(values.get("country", []))
    return branch.CompanyProfile(
        okved_code=values["okved"][0], okved_name=profile.activity, region=next(iter(values.get("region", [])), ""),
        workers_band=next(iter(values.get("workers", [])), ""), lifecycle=next(iter(values.get("stage", [])), ""),
        age_band=next(iter(values.get("age", [])), ""),
        relationship=_relationship(next(iter(values.get("client", [])), "")),
        products={_PRODUCTS[p] for p in values.get("product", []) if p in _PRODUCTS},
        foreign_trade="factor:fx" in profile.tags or bool(countries), trade_countries=countries)


def _relationship(client_type: str) -> branch.Relationship:
    try:
        return branch.Relationship(client_type.lower())
    except ValueError:
        return branch.Relationship.PROSPECT


def _side(s: CounterpartySector) -> branch.CounterpartySector:
    return branch.CounterpartySector(division=s.division, name=s.name, share=min(max(s.share, 0.0), 1.0),
                                     counterparties=s.counterparties,
                                     foreign_trade_share=min(max(s.foreign_trade_share, 0.0), 1.0))


def _combined_shares(d: CompanyDossier) -> dict[str, float]:
    """Доля отрасли в обоих потоках вместе: для правил, которым сторона потока неважна."""
    shares: dict[str, float] = {}
    sides = [*d.suppliers, *d.buyers]
    for s in sides:
        shares[s.division] = shares.get(s.division, 0.0) + s.share
    total = sum(shares.values()) or 1.0
    return {division: round(value / total, 4) for division, value in shares.items()}


# --------------------------------------------------------------------------- ветка -> оркестратор
def banner_from_dto(dto: branch.BannerDTO) -> Banner:
    return Banner(
        banner_id=dto.banner_id, severity=Severity(dto.severity.value), label=dto.label, headline=dto.headline,
        for_you=dto.for_you, sources=tuple(_source(s) for s in dto.sources), expires_at=dto.expires_at,
        caveat=dto.caveat, raw_news=dto.raw_news, cluster_id=dto.cluster_id, placements=tuple(dto.placements))


def answer_from_branch(answer: branch.ChatAnswer) -> ChatAnswer:
    urls = {s.ref_id: s.url for s in answer.sources if s.ref_id}
    claims = tuple(Claim(kind=c.type.value, text=c.text,
                         source_urls=tuple(dict.fromkeys(urls[e.ref_id] for e in c.evidence if e.ref_id in urls)),
                         evidence=tuple(Evidence(e.ref_id, e.quote) for e in c.evidence), grounding=c.grounding)
                   for c in answer.claims)
    return ChatAnswer(answer_id=answer.answer_id, claims=claims, sources=tuple(_source(s) for s in answer.sources),
                      confidence=answer.confidence, caveat=answer.caveat, raw_news=answer.raw_news,
                      follow_ups=tuple(answer.follow_ups))


def _source(s: branch.SourceRef) -> SourceRef:
    return SourceRef(SourceKind(s.kind.value), s.title, s.url, s.ref_id)
