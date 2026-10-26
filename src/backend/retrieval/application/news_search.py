"""Bounded, read-only news retrieval for the orchestrator port.

Rules are an explicit lexical baseline, not a trained classifier or relevance labels.
"""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orchestrator.domain.models import CompanyProfile, NewsItem, RetrievalResult, SourceKind

from ..domain.headlines import headline_cluster_id
from ..domain.query import natural_query
from ..domain.search_rules import rules_for
from .event_context import build_event_context
from .ports import NewsSearchStore, ReportFileCatalog


class NewsSearch:
    def __init__(self, store: NewsSearchStore, report_files: ReportFileCatalog, *, timeout_s=0.65,
                 lookback_days=90, max_concurrency=4, bank_enabled=True, reports_root=None):
        if not 0 < timeout_s <= 5 or not 1 <= lookback_days <= 365 or not 1 <= max_concurrency <= 16:
            raise ValueError('Invalid retrieval bounds')
        self.store, self.report_files = store, report_files
        self.timeout_s, self.lookback_days = timeout_s, lookback_days
        self.bank_enabled = bank_enabled
        self.reports_root = Path(reports_root).resolve() if reports_root is not None else None
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def retrieve(self, query):
        if not isinstance(query.tags, (set, frozenset)) or len(query.tags) > 100 or any(
                not isinstance(t, str) or len(t) > 128 for t in query.tags):
            raise ValueError('Expected at most 100 tags, each at most 128 characters')
        if type(query.limit) is not int or not 1 <= query.limit <= 100:
            raise ValueError('limit must be 1..100')
        if query.question is not None and (not isinstance(query.question, str)
                or not query.question.strip() or len(query.question) > 2000):
            raise ValueError('question must be nonempty and at most 2000 characters')
        as_of = query.as_of or datetime.now(timezone.utc)
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise ValueError('Timezone-aware as_of required')
        since = query.published_after or as_of - timedelta(days=self.lookback_days)
        if any(not isinstance(t, datetime) or t.tzinfo is None for t in (as_of, since)) or since > as_of:
            raise ValueError('Timezone-aware timestamps required; published_after <= as_of')
        rules = rules_for(query.tags)
        selected = [r['query'] for r in rules if r['tag'] in query.tags and r['geo_pattern'] is None]
        geography = [r['geo_pattern'] for r in rules if r['tag'] in query.tags and r['geo_pattern']]
        geo_pattern = '|'.join(geography) if geography else None
        geo_search = ' OR '.join(r['query'] for r in rules if r['tag'] in query.tags and r['geo_pattern'])
        search = natural_query(query.question) if query.question is not None else ' OR '.join(selected)
        if not search and (query.question is not None or geo_pattern is None):
            return RetrievalResult(news=())
        async with asyncio.timeout(self.timeout_s):
            async with self._semaphore:
                async with self.store.session(max(1, int(self.timeout_s * 1000))) as session:
                    rows = await session.fetch_news(
                        rules=rules, search=search, geo_pattern=geo_pattern, geo_search=geo_search,
                        as_of=as_of, since=since, candidate_limit=min(max(query.limit*5, 50), 300),
                        tags=sorted(query.tags), tag_count=sum(r['tag'] in query.tags for r in rules),
                        chat=query.question is not None)
                    news, seen = [], set()
                    for row in rows:
                        # Conservative duplicate suppression, not semantic event clustering.
                        cluster_id = headline_cluster_id(row['title'], row['published_at'])
                        if cluster_id in seen:
                            continue
                        seen.add(cluster_id)
                        news.append(NewsItem(news_id=row['news_id'],cluster_id=cluster_id,title=row['title'],
                            text=row['text'],url=row['url'],published_at=row['published_at'],
                            source_kind=SourceKind(row['source_kind']),tags=frozenset(row['tags']),
                            retrieval_score=float(row['score'])))
                        if len(news) == query.limit:
                            break
                    bank = await self._bank(session, news, as_of) if news and self.bank_enabled else ()
                    return RetrievalResult(news=tuple(news), bank_materials=bank)

    async def _bank(self, session, news, as_of):
        merged = {}
        for event in news[:3]:
            context = await build_event_context(session, self.report_files, event,
                                                CompanyProfile('legacy','legacy',frozenset()),
                                                as_of=as_of,limit=2,reports_root=self.reports_root)
            for material in context.bank_materials:
                previous = merged.get(material.doc_id)
                merged[material.doc_id] = material if previous is None else replace(previous,
                    news_ids=tuple(sorted(set(previous.news_ids) | set(material.news_ids))))
        return tuple(merged.values())[:5]
