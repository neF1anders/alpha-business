"""Read-only lexical evidence retrieval over the team's insights.rag_chunks view."""
from datetime import date

from psycopg.rows import dict_row

from ingestion import database

from .query import natural_query


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


def retrieve(query, *, as_of, limit=5, published_after=None, source_files=None,
             include_undated=False, max_per_document=None, query_mode="websearch"):
    """Return source-linked chunks, not news or generated company-specific claims.

    No future reports or low-quality OCR pages are returned. Unknown publication
    dates require an explicit opt-in; date eligibility does not establish whether
    an older forecast has been superseded. Optional document caps diversify the
    results and should be evaluated against the uncapped baseline.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError('query must be a nonempty string of at most 2000 characters')
    if query_mode not in {'websearch', 'natural'}:
        raise ValueError('query_mode must be websearch or natural')
    if type(as_of) is not date:
        raise ValueError('as_of must be an explicit datetime.date')
    if published_after is not None and (type(published_after) is not date or published_after > as_of):
        raise ValueError('published_after must be a date no later than as_of')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be an integer from 1 to 100')
    if type(include_undated) is not bool:
        raise ValueError('include_undated must be boolean')
    if max_per_document is not None and (type(max_per_document) is not int or not 1 <= max_per_document <= 100):
        raise ValueError('max_per_document must be an integer from 1 to 100')
    if source_files is not None:
        if not isinstance(source_files, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in source_files
        ):
            raise ValueError('source_files must be a list of filenames')
        if not source_files:
            return []
    prepared = natural_query(query) if query_mode == 'natural' else query.strip()
    if not prepared:
        return []
    params = dict(query=prepared, as_of=as_of, since=published_after,
                  sources=source_files, undated=include_undated,
                  per_document=max_per_document, limit=limit)
    with database.connect() as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        connection.execute("SET LOCAL statement_timeout = '5s'")
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(NATURAL_SQL if query_mode == 'natural' else SQL, params)
            return cursor.fetchall()
