"""Замена ветки саммари без LLM: шаблонный текст из новости и признаков компании.

Нужна, чтобы сквозной сценарий работал без шлюза моделей. Реальная ветка (`summary_branch`)
подключается адаптером к тому же порту `SummaryBranch`.
"""
from __future__ import annotations

from datetime import timedelta

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
    return (SourceRef(r.item.source_kind, r.item.title, r.item.url),)


class TemplateSummaryBranch:
    async def banners(self, req: SummaryRequest) -> list[Banner]:
        out: list[Banner] = []
        for r in req.news:
            factor = _factor(r)
            severity = _FACTOR_SEVERITY.get(factor, Severity.WATCH)
            label = {Severity.ATTENTION: "Внимание", Severity.WATCH: "Наблюдение",
                     Severity.OPPORTUNITY: "Возможность"}[severity]
            if factor:
                label = f"{label} · {_FACTOR_LABEL[factor]}"
            out.append(Banner(
                banner_id=f"{req.company.company_id}:{r.item.cluster_id}", severity=severity, label=label,
                headline=r.item.title[:60], for_you=self._for_you(req), sources=_sources(r),
                expires_at=utcnow() + timedelta(hours=36)))
        return out

    async def chat(self, req: SummaryRequest) -> ChatAnswer:
        top = req.news[0]
        claims = [Claim("fact", top.item.title, (top.item.url,))]
        sources = list(_sources(top))
        for m in req.bank_materials[:1]:
            claims.append(Claim("bank", m.claim_text, (m.url,)))
            sources.append(SourceRef(SourceKind.BANK, m.title, m.url))
        claims.append(Claim("model", self._for_you(req)))
        return ChatAnswer(answer_id=f"{req.trace_id}:chat", claims=tuple(claims), sources=tuple(sources),
                          confidence=round(top.score, 2), caveat=True,
                          follow_ups=("Как это повлияет на мои кредиты?", "Что делали бы компании моей отрасли?"))

    @staticmethod
    def _for_you(req: SummaryRequest) -> str:
        features = req.company.features
        if not features:
            return "Событие может быть важно для вашего бизнеса."
        return f"Учтено по профилю компании: {features[0]}."
