"""Синтетические новости и retriever над ними: для демо и тестов, пока retrieval команды не подключён.

Теги новостей берутся из тех же пространств, что и теги компаний (docs/tags.md).
Тексты выдуманы для проверки сценария и не являются реальными новостями.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional

from ..domain.models import BankMaterial, NewsItem, NewsQuery, RetrievalResult, SourceKind, utcnow

_WORD = re.compile(r"\w+", re.UNICODE)


def sample_news(now: Optional[datetime] = None) -> list[NewsItem]:
    now = now or utcnow()

    def item(n: int, days: float, title: str, text: str, tags: set[str], kind: SourceKind,
             cluster: Optional[str] = None) -> NewsItem:
        return NewsItem(news_id=f"n{n}", cluster_id=cluster or f"c{n}", title=title, text=text,
                        url=f"https://example.org/news/{n}", published_at=now - timedelta(days=days),
                        source_kind=kind, tags=frozenset(tags))

    return [
        item(1, 1, "Банк России сохранил ключевую ставку на уровне 14%",
             "Совет директоров Банка России сохранил ключевую ставку 14% годовых после серии снижений.",
             {"factor:key_rate"}, SourceKind.PRIMARY),
        item(2, 2, "Эксперты: ставка останется высокой до конца года",
             "Аналитики ожидают, что ключевая ставка не изменится в ближайшие месяцы.",
             {"factor:key_rate"}, SourceKind.MEDIA, cluster="c1"),
        item(3, 3, "Рубль ослаб к юаню на фоне роста импорта",
             "Курс юаня вырос, импортёры закупают валюту для оплаты поставок из Китая.",
             {"factor:fx", "country:КИТАЙ"}, SourceKind.MEDIA),
        item(4, 4, "Цены на металлы выросли на 6% за месяц",
             "Подорожали сталь и алюминий, отрасли с сырьевой зависимостью отмечают рост затрат.",
             {"factor:commodities", "sector:manufacturing"}, SourceKind.MEDIA),
        item(5, 2, "Потребительский спрос остаётся на пике",
             "Оборот розничной торговли продолжает расти, потребление поддерживают зарплаты.",
             {"factor:consumer_demand", "sector:trade"}, SourceKind.BANK),
        item(7, 6, "Спрос на новостройки снижается при дорогой ипотеке",
             "Застройщики сообщают о сокращении продаж, часть проектов переносится.",
             {"factor:construction_market", "sector:construction", "sector:real_estate"}, SourceKind.MEDIA),
        item(8, 40, "Итоги прошлого квартала в отрасли транспорта",
             "Старая сводка о грузоперевозках за прошлый квартал.", {"sector:transport"}, SourceKind.MEDIA),
    ]


def sample_bank_materials() -> tuple[BankMaterial, ...]:
    return (
        BankMaterial("alfa-keyrate", "Ключевая ставка ЦБ РФ: ставка без изменения на 14,0%",
                     "Риторика ЦБ стала жёстче, быстрое снижение ставки маловероятно.",
                     "https://example.org/alfa/keyrate", 1),
    )


class InMemoryNewsRetriever:
    """Фильтр по тегам (баннеры) или по словам вопроса (чат). Оценка 0..1 как доля совпавших признаков."""

    def __init__(self, news: Optional[list[NewsItem]] = None,
                 bank_materials: Optional[tuple[BankMaterial, ...]] = None) -> None:
        self._news = news if news is not None else sample_news()
        self._bank = bank_materials if bank_materials is not None else sample_bank_materials()

    async def retrieve(self, query: NewsQuery) -> RetrievalResult:
        words = {w.lower() for w in _WORD.findall(query.question or "") if len(w) > 3}
        scored: list[tuple[float, NewsItem]] = []
        for it in self._news:
            tag_hit = len(it.tags & query.tags) / len(it.tags) if it.tags and query.tags else 0.0
            haystack = f"{it.title} {it.text}".lower()
            word_hit = (sum(1 for w in words if w[:5] in haystack) / len(words)) if words else 0.0
            score = max(tag_hit, word_hit)
            if (query.question is None and tag_hit > 0) or (query.question is not None and (word_hit > 0 or tag_hit > 0)):
                scored.append((score, it))
        scored.sort(key=lambda p: -p[0])
        news = tuple(replace(it, retrieval_score=s) for s, it in scored[:query.limit])
        return RetrievalResult(news=news, bank_materials=self._bank if news else ())
