"""PostgreSQL full-text search over the insights.rag_chunks view (read-only)."""
from psycopg.rows import dict_row

from ingestion.infrastructure import postgres


SQL = """
WITH candidates AS (
    SELECT chunk_id, document_id, title, source_file, published_at, date_source,
           page_number, text, start_char, end_char, method, confidence,
           ts_rank_cd(search_vector, websearch_to_tsquery('russian', %(query)s)) AS score
    FROM insights.rag_chunks
    WHERE source_type = 'pdf'
      AND search_vector @@ websearch_to_tsquery('russian', %(query)s)
      AND ((published_at <= %(as_of)s::date
            AND (%(since)s::date IS NULL OR published_at >= %(since)s::date))
           OR (%(undated)s AND published_at IS NULL))
      AND (%(sources)s::text[] IS NULL OR source_file = ANY(%(sources)s::text[]))
), diversified AS (
    SELECT *, row_number() OVER (
        PARTITION BY document_id ORDER BY score DESC, chunk_id
    ) AS document_rank
    FROM candidates
)
SELECT chunk_id, document_id, title, source_file, published_at, date_source,
       page_number, text, start_char, end_char, method, confidence, score
FROM diversified
WHERE (%(per_document)s::int IS NULL OR document_rank <= %(per_document)s::int)
ORDER BY score DESC, published_at DESC NULLS LAST, chunk_id
LIMIT %(limit)s
"""


# Enrich each chunk with its page heading, so an entity mentioned at the start
# of a page also identifies the continuation chunk. Nothing is written to the DB.
NATURAL_SQL = r"""
WITH terms AS (
    SELECT unnest(tsvector_to_array(to_tsvector('russian', %(query)s))) AS word
), model AS (
    SELECT string_agg(quote_literal(word), ' | ')::tsquery AS query,
           count(*) AS term_count FROM terms
), eligible AS (
    SELECT c.*,
           c.search_vector
             || setweight(to_tsvector('russian', left(p.text, 400)), 'A')
             || setweight(to_tsvector('russian', c.title), 'A') AS contextual_vector
    FROM insights.rag_chunks c
    JOIN insights.document_pages p USING(document_id, page_number)
    WHERE c.source_type = 'pdf'
      AND ((c.published_at <= %(as_of)s::date
            AND (%(since)s::date IS NULL OR c.published_at >= %(since)s::date))
           OR (%(undated)s AND c.published_at IS NULL))
      AND (%(sources)s::text[] IS NULL OR c.source_file = ANY(%(sources)s::text[]))
      AND NOT (p.text ~* '^\s*(контакты\s*(/|и)\s*дисклеймер|дисклеймер|©\s*альфа)')
), matched AS (
    SELECT e.*, m.term_count AS query_term_count,
           (SELECT count(*) FROM terms t
            WHERE t.word = ANY(tsvector_to_array(e.contextual_vector))) AS matched_terms,
           ts_rank_cd(e.contextual_vector, m.query, 32) AS score
    FROM eligible e CROSS JOIN model m
    WHERE e.contextual_vector @@ m.query
), diversified AS (
    SELECT *, row_number() OVER (
        PARTITION BY document_id ORDER BY matched_terms DESC, score DESC, chunk_id
    ) AS document_rank FROM matched
)
SELECT chunk_id, document_id, title, source_file, published_at, date_source,
       page_number, text, start_char, end_char, method, confidence, score,
       matched_terms, query_term_count
FROM diversified
WHERE (%(per_document)s::int IS NULL OR document_rank <= %(per_document)s::int)
ORDER BY matched_terms DESC, score DESC, published_at DESC NULLS LAST, chunk_id
LIMIT %(limit)s
"""


class PostgresDocumentStore:
    def search_chunks(self, *, query, natural, as_of, since, sources, undated, per_document, limit):
        params = dict(query=query, as_of=as_of, since=since,
                      sources=sources, undated=undated,
                      per_document=per_document, limit=limit)
        with postgres.connect() as connection:
            connection.execute('SET TRANSACTION READ ONLY')
            connection.execute("SET LOCAL statement_timeout = '5s'")
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(NATURAL_SQL if natural else SQL, params)
                return cursor.fetchall()
