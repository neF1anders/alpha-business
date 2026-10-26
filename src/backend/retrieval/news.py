"""Bounded, read-only PostgreSQL news retrieval for the orchestrator port.

Rules are an explicit lexical baseline, not a trained classifier or relevance labels.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from orchestrator.domain.models import NewsItem, RetrievalResult, SourceKind
from .query import natural_query

# Shared tag vocabulary: docs/tags.md. Each phrase is a literal FTS phrase.
TAG_PHRASES = {
    'factor:key_rate': ('ключевая ставка', 'ставка ЦБ', 'денежно-кредитная политика', 'стоимость кредитов'),
    'factor:fx': ('курс рубля', 'курс доллара', 'курс юаня', 'валютный рынок', 'валютные платежи'),
    'factor:labor': ('рынок труда', 'дефицит кадров', 'заработная плата', 'безработица'),
    'factor:commodities': ('цена нефти', 'цены на нефть', 'цены на металлы', 'сталь', 'зерно', 'золото'),
    'factor:consumer_demand': ('потребительский спрос', 'розничные продажи', 'потребительские расходы'),
    'factor:construction_market': ('новостройки', 'ипотека', 'жилищное строительство', 'застройщики'),
    'sector:agro': ('сельское хозяйство', 'аграрный', 'урожай', 'зерно'),
    'sector:mining': ('добыча', 'горнодобывающий', 'рудник'),
    'sector:manufacturing': ('промышленность', 'производство', 'завод', 'металлургия'),
    'sector:energy': ('энергетика', 'электроэнергия', 'электростанция'),
    'sector:utilities': ('ЖКХ', 'коммунальные услуги', 'водоснабжение'),
    'sector:construction': ('строительство', 'застройщики', 'стройматериалы'),
    'sector:trade': ('ритейл', 'розничная торговля', 'магазин', 'маркетплейс'),
    'sector:transport': ('грузоперевозки', 'логистика', 'железная дорога', 'авиаперевозки'),
    'sector:horeca': ('ресторан', 'гостиница', 'общепит', 'отель'),
    'sector:it_telecom': ('телеком', 'связь', 'интернет', 'программное обеспечение', 'дата-центр'),
    'sector:finance': ('банк', 'страхование', 'кредитование', 'финансовый рынок'),
    'sector:real_estate': ('недвижимость', 'аренда', 'жилье'),
    'sector:prof_services': ('консалтинг', 'аудит', 'юридические услуги'),
    'sector:admin_services': ('аутсорсинг', 'кадровое агентство', 'клининг'),
    'sector:public': ('госзакупки', 'государственный бюджет'),
    'sector:education': ('образование', 'университет', 'школа'),
    'sector:health': ('здравоохранение', 'лекарство', 'клиника', 'фармацевтика'),
    'sector:culture_sport': ('кинотеатр', 'театр', 'спорт', 'музей'),
    'sector:other_services': ('ремонт техники', 'бытовые услуги'),
}


def phrase_query(phrases):
    # Strip operators from external geography tags, rather than treating them as FTS syntax.
    cleaned = [' '.join(re.findall(r'[\w]+', phrase, flags=re.UNICODE)) for phrase in phrases]
    return ' OR '.join('"'+value+'"' for value in cleaned if value)


# Explicit geography aliases, never a Russian stem (Дания and данные both stem to дан).
GEO_ALIASES = {
    'дания': ('дания', 'дании', 'данию', 'данией'),
    'китай': ('китай', 'китая', 'китае', 'китаю', 'китаем'),
    'россия': ('россия', 'россии', 'россию', 'россией'),
    'москва': ('москва', 'москвы', 'москве', 'москву', 'москвой'),
    'санкт-петербург': ('санкт-петербург', 'санкт-петербурга', 'санкт-петербурге', 'санкт-петербургу', 'санкт-петербургом'),
    'южная корея': ('южная корея', 'южной кореи', 'южной корее', 'южную корею', 'южной кореей'),
}


def geography_pattern(value):
    value = ' '.join(value.casefold().replace('ё','е').split())
    forms = GEO_ALIASES.get(value, (value,))
    # PostgreSQL ARE word boundaries; escaping makes external profile values literal.
    return r'\m(?:' + '|'.join(re.escape(f) for f in forms) + r')\M'


def rules_for(tags):
    rules = dict(TAG_PHRASES)
    for tag in tags:
        namespace, _, value = tag.partition(':')
        if namespace in ('country', 'region') and len(value.strip()) >= 3:
            rules[tag] = (value,)
    return [{'tag': tag, 'query': phrase_query(phrases),
             'geo_pattern': geography_pattern(tag.partition(':')[2])
             if tag.startswith(('country:', 'region:')) else None}
            for tag, phrases in sorted(rules.items())]


NEWS_SQL = """
WITH rules AS MATERIALIZED (
    SELECT tag, websearch_to_tsquery('russian', query) AS query, geo_pattern
    FROM jsonb_to_recordset(%(rules)s::jsonb) AS r(tag text, query text, geo_pattern text)
), model AS (
    SELECT websearch_to_tsquery('russian', %(search)s) AS query,
           websearch_to_tsquery('russian', %(geo_search)s) AS geo_query
), candidates AS MATERIALIZED (
    SELECT n.news_id,n.title,n.text,n.url,n.source_kind,n.published_at,n.search_vector,
           ts_rank_cd(n.search_vector, m.query, 32) AS lexical_score
    FROM insights.news_articles n CROSS JOIN model m
    WHERE (n.search_vector @@ m.query OR (NOT %(chat)s AND %(geo_pattern)s::text IS NOT NULL
           AND n.search_vector @@ m.geo_query AND replace(lower(n.title || ' ' || n.text),'ё','е') ~ %(geo_pattern)s))
      AND n.published_at <= %(as_of)s AND n.published_at >= %(since)s
    ORDER BY lexical_score DESC,n.published_at DESC,n.news_id
    LIMIT %(candidate_limit)s
), tagged AS (
    SELECT c.*, ARRAY(SELECT r.tag FROM rules r WHERE
        CASE WHEN r.geo_pattern IS NULL THEN c.search_vector @@ r.query
             ELSE replace(lower(c.title || ' ' || c.text),'ё','е') ~ r.geo_pattern END
        ORDER BY r.tag) AS tags
    FROM candidates c
), scored AS (
    SELECT t.*, (SELECT count(*) FROM unnest(t.tags) x WHERE x = ANY(%(tags)s::text[])) AS tag_hits
    FROM tagged t
)
SELECT news_id,title,text,url,source_kind,published_at,tags,
       CASE WHEN %(chat)s THEN 0.8*lexical_score + 0.2*tag_hits/GREATEST(%(tag_count)s,1)
            ELSE 0.4*lexical_score + 0.6*tag_hits/GREATEST(%(tag_count)s,1) END AS score
FROM scored
WHERE %(chat)s OR tag_hits > 0
ORDER BY score DESC,published_at DESC,news_id
LIMIT %(candidate_limit)s
"""


async def open_connection():
    # Never load .env implicitly or log connection strings. Standard PG* variables also work.
    return await psycopg.AsyncConnection.connect(
        os.environ.get('RETRIEVAL_DATABASE_URL', os.environ.get('DATABASE_URL', '')),
        connect_timeout=2, row_factory=dict_row)


class PostgresNewsRetriever:
    def __init__(self, *, timeout_s=0.65, lookback_days=90, max_concurrency=4, bank_enabled=True, reports_root=None):
        if not 0 < timeout_s <= 5 or not 1 <= lookback_days <= 365 or not 1 <= max_concurrency <= 16:
            raise ValueError('Invalid retrieval bounds')
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
        params = dict(rules=Jsonb(rules), search=search, geo_pattern=geo_pattern, geo_search=geo_search, as_of=as_of, since=since,
                      candidate_limit=min(max(query.limit*5, 50), 300), tags=sorted(query.tags),
                      tag_count=sum(r['tag'] in query.tags for r in rules), chat=query.question is not None)
        async with asyncio.timeout(self.timeout_s):
            async with self._semaphore:
                async with await open_connection() as conn:
                    await conn.execute('SET TRANSACTION READ ONLY')
                    await conn.execute("SELECT set_config('statement_timeout', %s, true)",
                                       (str(max(1,int(self.timeout_s*1000))),))
                    cursor = await conn.execute(NEWS_SQL, params)
                    rows = await cursor.fetchall()
                    news, seen = [], set()
                    for row in rows:
                        # Conservative duplicate suppression, not semantic event clustering.
                        normalized = ' '.join(re.findall(r'\w+', row['title'].casefold().replace('ё','е')))
                        key = normalized + '|' + row['published_at'].astimezone(timezone.utc).date().isoformat()
                        cluster_id = 'headline:' + hashlib.sha256(key.encode()).hexdigest()[:24]
                        if cluster_id in seen:
                            continue
                        seen.add(cluster_id)
                        news.append(NewsItem(news_id=row['news_id'],cluster_id=cluster_id,title=row['title'],
                            text=row['text'],url=row['url'],published_at=row['published_at'],
                            source_kind=SourceKind(row['source_kind']),tags=frozenset(row['tags']),
                            retrieval_score=float(row['score'])))
                        if len(news) == query.limit:
                            break
                    bank = await self._bank(conn, query.question, news, as_of) if news and self.bank_enabled else ()
                    return RetrievalResult(news=tuple(news), bank_materials=bank)

    async def _bank(self, conn, question, news, as_of):
        from dataclasses import replace
        from orchestrator.domain.models import CompanyProfile
        from .events import event_context
        merged = {}
        for event in news[:3]:
            context = await event_context(conn, event, CompanyProfile('legacy','legacy',frozenset()),
                                          as_of=as_of,limit=2,reports_root=self.reports_root)
            for material in context.bank_materials:
                previous = merged.get(material.doc_id)
                merged[material.doc_id] = material if previous is None else replace(previous,
                    news_ids=tuple(sorted(set(previous.news_ids) | set(material.news_ids))))
        return tuple(merged.values())[:5]
