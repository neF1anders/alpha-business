"""Реранкер под профиль компании: теги, свежесть, тип источника. Без ML и без сети.

Теги новости и компании совпадают по равенству строк (пространства factor, sector, okved, country, region).
Тот же порт `Reranker` позже может реализовать cross-encoder.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .models import CompanyProfile, NewsItem, RankedNews, SourceKind, utcnow

# Эвристические веса тегов; не вероятности влияния на компанию.
NAMESPACE_WEIGHT = {"factor": 0.7, "sector": 0.5, "okved": 0.4, "country": 0.3, "region": 0.2}

# (тег новости, тег компании, вес): модификаторы по продуктам и масштабу.
MODIFIERS: tuple[tuple[str, str, float], ...] = (
    ("factor:key_rate", "product:credit", 0.25),
    ("factor:key_rate", "product:leasing", 0.2),
    ("factor:key_rate", "product:factoring", 0.2),
    ("factor:key_rate", "product:deposit", 0.1),
    ("factor:labor", "workers:Более 251", 0.1),
)

SOURCE_WEIGHT = {SourceKind.PRIMARY: 1.0, SourceKind.BANK: 0.9, SourceKind.MEDIA: 0.6}

W_RELEVANCE, W_RETRIEVAL, W_FRESHNESS, W_SOURCE = 0.55, 0.20, 0.15, 0.10


class TagReranker:
    def __init__(self, half_life_days: float = 7.0, min_score: float = 0.15, min_relevance: float = 0.05) -> None:
        self._half_life = half_life_days
        self._min_score = min_score
        self._min_relevance = min_relevance

    def rerank(self, profile: CompanyProfile, items: list[NewsItem], limit: int, *,
               require_tag_match: bool = True, now: Optional[datetime] = None) -> list[RankedNews]:
        """Лучшая новость на кластер, по убыванию оценки, не больше `limit`.

        `require_tag_match=False` для чата: новости уже отобраны по вопросу, совпадение тегов не обязательно.
        """
        now = now or utcnow()
        best: dict[str, RankedNews] = {}
        for item in items:
            ranked = self._score(profile, item, now, require_tag_match)
            if ranked is None:
                continue
            cur = best.get(item.cluster_id)
            if cur is None or ranked.score > cur.score:
                best[item.cluster_id] = ranked
        ordered = sorted(best.values(), key=lambda r: (-r.score, r.item.news_id))
        return ordered[:limit]

    def _score(self, profile: CompanyProfile, item: NewsItem, now: datetime,
               require_tag_match: bool) -> Optional[RankedNews]:
        from .news_quality import historical_retrospective
        if item.published_at > now or (require_tag_match and historical_retrospective(item.title, item.text, now)):
            return None
        matched = sorted(item.tags & profile.tags)
        miss = 1.0
        used: list[str] = []
        for tag in matched:
            weight = NAMESPACE_WEIGHT.get(tag.split(":", 1)[0])
            if weight is not None:
                miss *= 1 - weight
                used.append(tag)
        for news_tag, company_tag, weight in MODIFIERS:
            if news_tag in item.tags and company_tag in profile.tags:
                miss *= 1 - weight
        relevance = 1 - miss
        if require_tag_match and relevance < self._min_relevance:
            return None

        age_days = max((now - item.published_at).total_seconds() / 86400, 0.0)
        freshness = 0.5 ** (age_days / self._half_life)
        source = SOURCE_WEIGHT[item.source_kind]
        score = (W_RELEVANCE * relevance + W_RETRIEVAL * min(max(item.retrieval_score, 0.0), 1.0)
                 + W_FRESHNESS * freshness + W_SOURCE * source)
        if score < self._min_score:
            return None
        reasons = (f"relevance={relevance:.2f}", f"freshness={freshness:.2f}", f"source={source:.1f}")
        return RankedNews(item=item, score=round(min(score, 1.0), 4), matched_tags=tuple(used), reasons=reasons)
