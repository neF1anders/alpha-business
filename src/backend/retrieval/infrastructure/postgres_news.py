"""PostgreSQL adapter for bounded, read-only news and event-context queries."""
from contextlib import asynccontextmanager

from psycopg.types.json import Jsonb

from .connection import open_connection
from .postgres_documents import NATURAL_SQL

NEWS_SQL = """
WITH rules AS MATERIALIZED (
    SELECT tag, websearch_to_tsquery('russian', query) AS query, geo_pattern
    FROM jsonb_to_recordset(%(rules)s::jsonb) AS r(tag text, query text, geo_pattern text)
), terms AS (
    -- Chat: lemmas of the question; a question rarely repeats an article word for word.
    SELECT DISTINCT unnest(tsvector_to_array(to_tsvector('russian', %(search)s))) AS word
    WHERE %(chat)s
), model AS (
    SELECT CASE WHEN %(chat)s THEN (SELECT string_agg(quote_literal(word), ' | ')::tsquery FROM terms)
                ELSE websearch_to_tsquery('russian', %(search)s) END AS query,
           websearch_to_tsquery('russian', %(geo_search)s) AS geo_query,
           (SELECT count(*) FROM terms) AS term_count
), matched AS MATERIALIZED (
    SELECT n.news_id,n.title,n.text,n.url,n.source_kind,n.published_at,n.search_vector,
           ts_rank_cd(n.search_vector, m.query, 32) AS lexical_score,
           CASE WHEN %(chat)s THEN (SELECT count(*) FROM terms t
                WHERE t.word = ANY(tsvector_to_array(n.search_vector))) ELSE 0 END AS matched_terms,
           -- A long article contains most words of any question; the headline says what it is about.
           CASE WHEN %(chat)s THEN (SELECT count(*) FROM terms t
                WHERE t.word = ANY(tsvector_to_array(to_tsvector('russian', n.title)))) ELSE 0 END AS title_terms,
           m.term_count
    FROM insights.news_articles n CROSS JOIN model m
    WHERE (n.search_vector @@ m.query OR (NOT %(chat)s AND %(geo_pattern)s::text IS NOT NULL
           AND n.search_vector @@ m.geo_query AND replace(lower(n.title || ' ' || n.text),'ё','е') ~ %(geo_pattern)s))
      AND n.published_at <= %(as_of)s AND n.published_at >= %(since)s
), candidates AS MATERIALIZED (
    -- OR alone matches almost everything: a chat answer needs half of the question's terms,
    -- all of them for a one- or two-word question (a single stem may collide, e.g. Дания/данные).
    SELECT * FROM matched
    WHERE NOT %(chat)s OR matched_terms >= CASE WHEN term_count <= 2 THEN term_count
                                                ELSE ceil(term_count / 2.0) END
    ORDER BY matched_terms + title_terms DESC,lexical_score DESC,published_at DESC,news_id
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
       CASE WHEN %(chat)s THEN (0.35*matched_terms + 0.4*title_terms)/GREATEST(term_count,1)
                               + 0.15*lexical_score + 0.1*tag_hits/GREATEST(%(tag_count)s,1)
            ELSE 0.4*lexical_score + 0.6*tag_hits/GREATEST(%(tag_count)s,1) END AS score
FROM scored
WHERE %(chat)s OR tag_hits > 0
ORDER BY score DESC,published_at DESC,news_id
LIMIT %(candidate_limit)s
"""

DETECT_SQL = """
WITH vectors AS (
 SELECT to_tsvector('russian',%(title)s) AS headline,
        to_tsvector('russian',%(lead)s) AS lead,
        replace(lower(%(title)s),'ё','е') AS raw_title,
        replace(lower(%(lead)s),'ё','е') AS raw_lead
), rules AS (
 SELECT tag,websearch_to_tsquery('russian',query) AS q,geo_pattern
 FROM jsonb_to_recordset(%(rules)s::jsonb) AS r(tag text,query text,geo_pattern text)
)
SELECT tag, CASE WHEN geo_pattern IS NULL THEN headline @@ q
            ELSE raw_title ~ geo_pattern END AS in_title FROM rules CROSS JOIN vectors
WHERE CASE WHEN geo_pattern IS NULL THEN headline @@ q OR lead @@ q
           ELSE raw_title ~ geo_pattern OR raw_lead ~ geo_pattern END ORDER BY tag
"""

# Anchor must occur in the actual chunk, not only in the document title or page
# heading. NATURAL_SQL still adds those fields for ordering and diversification.
EVENT_BANK_SQL = NATURAL_SQL.replace(
    'AND NOT (p.text',
    "AND c.search_vector @@ websearch_to_tsquery('russian', %(anchor)s)\n"
    "      AND length(regexp_replace(c.text, '[^[:alpha:]]', '', 'g'))::float / GREATEST(length(c.text),1) >= 0.65\n"
    "      AND NOT (p.text",
).replace(
    'ORDER BY matched_terms DESC, score DESC',
    "ORDER BY ts_rank_cd(to_tsvector('russian', title), websearch_to_tsquery('russian', %(anchor)s),32) DESC, matched_terms DESC, score DESC",
)


class PostgresNewsSession:
    def __init__(self, connection):
        self._connection = connection

    async def fetch_news(self, *, rules, search, geo_pattern, geo_search, as_of, since,
                         candidate_limit, tags, tag_count, chat):
        params = dict(rules=Jsonb(rules), search=search, geo_pattern=geo_pattern, geo_search=geo_search, as_of=as_of, since=since,
                      candidate_limit=candidate_limit, tags=tags, tag_count=tag_count, chat=chat)
        cursor = await self._connection.execute(NEWS_SQL, params)
        return await cursor.fetchall()

    async def detect_topics(self, *, title, lead, rules):
        params = dict(title=title, lead=lead, rules=Jsonb(rules))
        return await (await self._connection.execute(DETECT_SQL, params)).fetchall()

    async def fetch_bank_chunks(self, *, query, anchor, as_of, since, limit):
        params = dict(query=query, anchor=anchor, as_of=as_of, since=since, sources=None,
                      undated=False, per_document=2, limit=limit)
        return await (await self._connection.execute(EVENT_BANK_SQL, params)).fetchall()


class PostgresNewsStore:
    @asynccontextmanager
    async def session(self, statement_timeout_ms):
        async with await open_connection() as conn:
            await conn.execute('SET TRANSACTION READ ONLY')
            await conn.execute("SELECT set_config('statement_timeout', %s, true)",
                               (str(statement_timeout_ms),))
            yield PostgresNewsSession(conn)
