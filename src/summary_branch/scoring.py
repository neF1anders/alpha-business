"""Уверенность без LLM-судьи, метрика качества саммари (SQS), подстановка «для вас», приоритет баннеров.

Два уровня расчёта:
  content_signals()    — один раз на (кластер, сегмент): обоснованность, источники, состав утверждений, SQS.
  company_confidence() — на каждую компанию сегмента: релевантность и свежесть + сигналы сегмента.
Все функции чистые и бесплатные по токенам. Необязательные усилители (NLI, калибратор) передаются callable.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Callable, Optional

from .models import (
    Banner, Claim, ClaimType, CompanyContext, Confidence, ConfidenceComponents, ContentSignals,
    Decision, LeadRef, RankedNewsItem, SourceKind, SourceRef, SummaryDraft, SummaryRequest, utcnow,
)

# --------------------------------------------------------------------------- параметры (вынести в конфиг)
SOURCE_PRIOR = {SourceKind.PRIMARY: 1.0, SourceKind.BANK: 0.9, SourceKind.MEDIA: 0.6}
WEIGHTS = {"relevance": 0.30, "grounding": 0.35, "source_quality": 0.15, "freshness": 0.10, "claim_mix": 0.10}
HIGH, MEDIUM = 0.75, 0.55
HALF_LIFE_H = {"rate": 72.0, "fx": 24.0, "default": 48.0}
SEVERITY_WEIGHT = {"attention": 1.0, "watch": 0.7, "opportunity": 0.8}
HEADLINE_MAX, FOR_YOU_MAX = 60, 140

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_WORD = re.compile(r"[а-яёa-z]{4,}", re.I)


# --------------------------------------------------------------------------- вспомогательное
def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _numbers(text: str) -> set[str]:
    out = set()
    for m in _NUM.findall(text):
        v = m.replace(",", ".")
        out.add(v.rstrip("0").rstrip(".") if "." in v else v)
    return out


def _stems(text: str) -> set[str]:
    return {w.lower()[:5] for w in _WORD.findall(text)}


def lead_from_news(n: RankedNewsItem) -> LeadRef:
    return LeadRef(cluster_id=n.cluster_id, published_at=n.published_at, tags=n.tags, rerank_score=n.rerank_score)


def freshness(published_at: datetime, now: datetime, topic: str = "default") -> float:
    hours = max((now - published_at).total_seconds() / 3600.0, 0.0)
    return _clip(0.5 ** (hours / HALF_LIFE_H.get(topic, HALF_LIFE_H["default"])))


def tag_overlap(company_tags: list[str], news_tags: list[str]) -> float:
    a, b = set(map(str.lower, company_tags)), set(map(str.lower, news_tags))
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


# --------------------------------------------------------------------------- обоснованность
def claim_grounding(claim: Claim, evidence_text: str,
                    nli: Optional[Callable[[str, str], float]] = None) -> float:
    """0.6 x доля чисел утверждения, найденных в источнике + 0.4 x доля основ слов. NLI усредняется, если задан."""
    nums = _numbers(claim.text)
    num_score = 1.0 if not nums else len(nums & _numbers(evidence_text)) / len(nums)
    cs, es = _stems(claim.text), _stems(evidence_text)
    lex_score = 1.0 if not cs else len(cs & es) / len(cs)
    base = 0.6 * num_score + 0.4 * lex_score
    if nli is not None:
        base = 0.5 * base + 0.5 * _clip(nli(evidence_text, claim.text))
    return _clip(base)


def _evidence_text(claim: Claim, req: SummaryRequest) -> str:
    by_news = {n.news_id: n for n in req.news}
    by_doc = {m.doc_id: m for m in req.bank_materials}
    parts: list[str] = []
    for e in claim.evidence:
        if e.quote:
            parts.append(e.quote)
        if e.ref_id in by_news:
            parts.append(by_news[e.ref_id].title + " " + by_news[e.ref_id].text)
        if e.ref_id in by_doc:
            parts.append(by_doc[e.ref_id].claim_text)
    return " ".join(parts)


# --------------------------------------------------------------------------- уровень сегмента
def _brevity(headline: str, for_you: str) -> float:
    def part(s: str, lim: int) -> float:
        return 1.0 if len(s) <= lim else _clip(1.0 - (len(s) - lim) / lim)
    return 0.5 * part(headline, HEADLINE_MAX) + 0.5 * part(for_you, FOR_YOU_MAX)


def _key_numbers(news: RankedNewsItem) -> set[str]:
    head = news.title + " " + " ".join(re.split(r"(?<=[.!?])\s+", news.text)[:2])
    return _numbers(head)


def content_signals(req: SummaryRequest, draft: SummaryDraft, claims: list[Claim], sources: list[SourceRef],
                    nli: Optional[Callable[[str, str], float]] = None) -> ContentSignals:
    """Считается один раз на (кластер, сегмент). Заполняет Claim.grounding."""
    checked = [c for c in claims if c.type in (ClaimType.FACT, ClaimType.BANK)]
    for c in checked:
        c.grounding = claim_grounding(c, _evidence_text(c, req), nli)
    grounding = sum(c.grounding or 0.0 for c in checked) / len(checked) if checked else 0.0
    numeric_miss = any(_numbers(c.text) and (c.grounding or 0) < 0.4 for c in checked)

    source_quality = max((SOURCE_PRIOR[s.kind] for s in sources), default=0.0)

    n = len(claims) or 1
    soft = [c for c in claims if c.type in (ClaimType.MODEL, ClaimType.HYPOTHESIS)]
    unsupported = sum(1 for c in soft if not c.evidence)
    claim_mix = _clip(1.0 - 0.6 * unsupported / n - 0.2 * len(soft) / n)

    faith = (sum(1 for c in checked if (c.grounding or 0) >= 0.6) / len(checked)) if checked else 0.0
    keys = _key_numbers(req.news[0])
    summary_text = " ".join([draft.headline] + [c.text for c in claims])
    coverage = 1.0 if not keys else len(keys & _numbers(summary_text)) / len(keys)
    spec = 1.0 if "{feature}" in draft.for_you_template else 0.0   # есть ли привязка к признаку компании
    brev = _brevity(draft.headline, draft.for_you_fallback)
    types = {c.type for c in claims}
    structure_ok = (bool(draft.headline) and bool(sources) and ClaimType.FACT in types
                    and all(c.evidence for c in checked))
    sqs = (0.45 * faith + 0.25 * coverage + 0.15 * spec + 0.15 * brev) if structure_ok else 0.0

    return ContentSignals(grounding=round(grounding, 3), source_quality=source_quality, claim_mix=round(claim_mix, 3),
                          numeric_miss=numeric_miss, faithfulness=round(faith, 3), coverage=round(coverage, 3),
                          specificity=spec, brevity=round(brev, 3), structure_ok=structure_ok,
                          sqs=round(_clip(sqs), 3))


# --------------------------------------------------------------------------- уровень компании
def company_confidence(signals: ContentSignals, lead: LeadRef, topic: str, company: CompanyContext,
                       has_sources: bool = True, now: Optional[datetime] = None,
                       calibrator: Optional[Callable[[float], float]] = None) -> Confidence:
    now = now or utcnow()
    reasons: list[str] = []
    relevance = _clip(0.6 * lead.rerank_score + 0.4 * tag_overlap(company.tags, lead.tags))
    fresh = freshness(lead.published_at, now, topic)
    comp = ConfidenceComponents(relevance=relevance, grounding=signals.grounding,
                                source_quality=signals.source_quality, freshness=fresh, claim_mix=signals.claim_mix)

    eps = 1e-3   # взвешенное геометрическое среднее: один провальный компонент тянет всё вниз
    score = math.exp(sum(w * math.log(max(getattr(comp, k), eps)) for k, w in WEIGHTS.items()))

    if signals.grounding < 0.6:
        reasons.append("факты плохо подтверждены источником")
    if signals.source_quality < 0.9:
        reasons.append("нет первоисточника или материалов банка")
    if signals.numeric_miss:
        score = min(score, 0.5)
        reasons.append("число из утверждения не найдено в источнике")
    if not has_sources:
        score = 0.0
        reasons.append("нет ни одного источника")
    if relevance < 0.25:
        score = min(score, 0.45)
        reasons.append("слабая связь новости с профилем компании")
    if calibrator is not None:
        score = calibrator(score)
    score = _clip(score)

    if score >= HIGH:
        decision = Decision.SHOW_SUMMARY
    elif score >= MEDIUM:
        decision = Decision.SHOW_WITH_CAVEAT
    elif score > 0:
        decision = Decision.SHOW_RAW_NEWS
    else:
        decision = Decision.DROP
    return Confidence(score=round(score, 3), decision=decision, components=comp, reasons=reasons)


# --------------------------------------------------------------------------- «для вас»
def render_for_you(template: str, fallback: str, company: CompanyContext, claims_text: str) -> str:
    """Подставляет в {feature} признак компании, лучше всего совпавший с текстом утверждений. Нет совпадения - fallback."""
    if "{feature}" not in template:
        return template
    ctx = _stems(claims_text + " " + template)
    best, best_ov = None, 0.0
    for f in company.features:
        fs = _stems(f)
        ov = len(fs & ctx) / len(fs) if fs else 0.0
        if ov > best_ov:
            best, best_ov = f, ov
    return template.format(feature=best) if best else fallback


# --------------------------------------------------------------------------- приоритет и выбор
def base_priority(severity: str, confidence: float) -> float:
    """Статическая часть. Идёт в score отсортированного множества Redis."""
    return round(SEVERITY_WEIGHT[severity] * confidence, 4)


def banner_priority(b: Banner, now: Optional[datetime] = None, seen: bool = False) -> float:
    """Динамическая часть при чтении: base x свежесть x новизна."""
    now = now or utcnow()
    hrs = max((now - b.created_at).total_seconds() / 3600.0, 0.0)
    fresh = 0.5 ** (hrs / HALF_LIFE_H.get(b.topic, HALF_LIFE_H["default"]))
    return round(base_priority(b.severity.value, b.confidence) * fresh * (0.4 if seen else 1.0), 4)


def pick_top_k(banners: list[Banner], k: int = 3, now: Optional[datetime] = None,
               seen_ids: Optional[set[str]] = None) -> list[Banner]:
    """Лучшие k без просроченных и DROP; повтор темы получает штраф 0.5 (жадный MMR)."""
    now = now or utcnow()
    seen_ids = seen_ids or set()
    pool = [b for b in banners if not b.is_expired(now) and b.decision != Decision.DROP]
    scored = {b.banner_id: banner_priority(b, now, b.banner_id in seen_ids) for b in pool}
    chosen: list[Banner] = []
    used: set[str] = set()
    while pool and len(chosen) < k:
        best = max(pool, key=lambda b: scored[b.banner_id] * (0.5 if b.topic in used else 1.0))
        chosen.append(best)
        used.add(best.topic)
        pool.remove(best)
    return chosen
