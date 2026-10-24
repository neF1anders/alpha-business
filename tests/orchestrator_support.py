"""Общие заглушки для тестов оркестратора."""
import asyncio
from datetime import datetime, timedelta, timezone

from orchestrator.domain.models import (
    Banner, BankMaterial, ChatAnswer, CompanyProfile, NewsItem, NewsQuery, RetrievalResult, Severity, SourceKind,
    SummaryRequest,
)
from orchestrator.infrastructure.template_branch import TemplateSummaryBranch

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)

PROFILE = CompanyProfile(
    company_id="U_1", segment_id="trade|mid", role="cfo",
    tags=frozenset({"sector:trade", "okved:47", "factor:key_rate", "factor:consumer_demand",
                    "product:credit", "region:Москва", "size:mid"}),
    features=("отрасль: торговля", "продукты банка: кредитные продукты"))


def news(n, tags, days=1, kind=SourceKind.MEDIA, cluster=None, score=0.5):
    return NewsItem(news_id=f"n{n}", cluster_id=cluster or f"c{n}", title=f"Новость {n}", text=f"Текст {n}",
                    url=f"https://example.org/{n}", published_at=NOW - timedelta(days=days), source_kind=kind,
                    tags=frozenset(tags), retrieval_score=score)


class FakeProfiles:
    def __init__(self, *profiles):
        self.by_id = {p.company_id: p for p in profiles}

    async def get(self, company_id):
        return self.by_id.get(company_id)


class FakeRetriever:
    def __init__(self, items=(), bank=(), error=None, delay=0.0):
        self.items, self.bank, self.error, self.delay = list(items), tuple(bank), error, delay
        self.queries = []

    async def retrieve(self, query: NewsQuery):
        self.queries.append(query)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return RetrievalResult(news=tuple(self.items), bank_materials=self.bank)


class FakeBranch(TemplateSummaryBranch):
    def __init__(self, error=None, delay=0.0):
        self.error, self.delay = error, delay
        self.requests: list[SummaryRequest] = []

    async def _gate(self, req):
        self.requests.append(req)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error

    async def banners(self, req):
        await self._gate(req)
        return await super().banners(req)

    async def chat(self, req):
        await self._gate(req)
        return await super().chat(req)


BANK = (BankMaterial("d1", "Обзор ставки", "Ставка без изменений", "https://example.org/bank", 1),)
