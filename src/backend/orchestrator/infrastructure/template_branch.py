"""Замена ветки саммари без LLM: шаблонный текст из новости и признаков компании.

Нужна, чтобы сквозной сценарий работал без шлюза моделей. Реальная ветка (`summary_branch`)
подключается адаптером к тому же порту `SummaryBranch`.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from ..domain.models import (
    Banner, ChatAnswer, Claim, RankedNews, Severity, SourceKind, SourceRef, SummaryRequest, utcnow,
)

_FACTOR_LABEL = {
    "factor:key_rate": "ставка", "factor:fx": "курс",
    "factor:commodities": "сырьё", "factor:consumer_demand": "спрос", "factor:construction_market": "стройка",
}
_FACTOR_SEVERITY = {"factor:key_rate": Severity.ATTENTION, "factor:fx": Severity.ATTENTION}


def _factor(r: RankedNews) -> str:
    return next((t for t in sorted(r.item.tags) if t in _FACTOR_LABEL), "")


def _sources(r: RankedNews) -> tuple[SourceRef, ...]:
    return (SourceRef(r.item.source_kind, r.item.title, r.item.url, r.item.news_id, r.item.content_kind),)


class TemplateSummaryBranch:
    async def banner(self, req: SummaryRequest) -> Optional[Banner]:
        r = req.news[0]
        factor = _factor(r)
        severity = _FACTOR_SEVERITY.get(factor, Severity.WATCH)
        label = {Severity.ATTENTION: "Внимание", Severity.WATCH: "Наблюдение",
                 Severity.OPPORTUNITY: "Возможность"}[severity]
        if factor:
            label = f"{label} · {_FACTOR_LABEL[factor]}"
        return Banner(
            banner_id=f"{req.company.company_id}:{r.item.cluster_id}", severity=severity, label=label,
            headline=r.item.title, for_you="", sources=_sources(r),
            expires_at=utcnow() + timedelta(hours=36), caveat=True, raw_news=True, cluster_id=r.item.cluster_id)

    async def chat(self, req: SummaryRequest) -> ChatAnswer:
        top = req.news[0]
        claims = [Claim("fact", top.item.title, (top.item.url,))]
        sources = list(_sources(top))
        relevant_bank = [m for m in req.bank_materials if top.item.news_id in m.news_ids or not m.news_ids]
        for m in relevant_bank[:1]:
            dated = f"Контекст из аналитики банка от {m.published_at.isoformat()}: " if m.published_at else ""
            claims.append(Claim("bank", dated + m.claim_text, (m.url,)))
            sources.append(SourceRef(SourceKind.BANK, m.title, m.url))
        # Retrieval score is not factual confidence; no generated impact claim in this fallback.
        return ChatAnswer(answer_id=f"{req.trace_id}:chat", claims=tuple(claims), sources=tuple(sources),
                          confidence=0.0, caveat=True, raw_news=True,
                          follow_ups=("Как это повлияет на мои кредиты?", "Что делали бы компании моей отрасли?"))
