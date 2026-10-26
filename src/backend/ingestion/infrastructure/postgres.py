"""PostgreSQL adapter: schema, CSV/document/news-snapshot loads and smoke search."""
import csv
import hashlib
import os
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from ..domain.news_snapshot import BRIEF_FIELDS, FIELDS, IMMUTABLE
from ..domain.taxonomy import FACTORS
from .filesystem import sha256
from ..domain.csv_rules import SOURCES

SCHEMA = Path(__file__).resolve().parents[4] / 'sql' / 'schema.sql'
SOURCE_RECORD, RAW_RECEIPT = FIELDS.index('source_record'), FIELDS.index('raw_receipt')


def connect():
    # Either DATABASE_URL or standard libpq PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD.
    return psycopg.connect(os.environ.get('DATABASE_URL', ''), connect_timeout=10)


def with_json(row):
    """Snapshot rows keep plain dicts; wrap the JSON columns only at the database boundary."""
    values = list(row)
    values[SOURCE_RECORD], values[RAW_RECEIPT] = Jsonb(values[SOURCE_RECORD]), Jsonb(values[RAW_RECEIPT])
    return tuple(values)


class PostgresDocumentStore:
    def initialize(self):
        with connect() as conn:
            conn.execute(SCHEMA.read_text(encoding='utf-8'))

    def replace_csv_snapshot(self, directory, manifest):
        with connect() as conn:
            # Serialize CSV writers. Snapshot replacement is committed only after all checks pass.
            conn.execute("SELECT pg_advisory_xact_lock(521101)")
            existing = conn.execute('SELECT manifest FROM insights.csv_snapshot WHERE singleton').fetchone()
            if existing and existing[0] == manifest:
                return {'status': 'unchanged'}
            for table, (_, columns) in SOURCES.items():
                fields = [c.lower() for c in columns] + ['source_row']
                conn.execute(sql.SQL('CREATE TEMP TABLE {} (LIKE insights.{}) ON COMMIT DROP').format(
                    sql.Identifier('stage_' + table), sql.Identifier(table)))
                statement = sql.SQL('COPY {} ({}) FROM STDIN').format(
                    sql.Identifier('stage_' + table), sql.SQL(',').join(map(sql.Identifier, fields)))
                count = 0
                with (directory / f'{table}.csv').open(encoding='utf-8', newline='') as stream, \
                        conn.cursor().copy(statement) as copy:
                    reader = csv.DictReader(stream, delimiter=';')
                    if reader.fieldnames != columns + ['SOURCE_ROW']:
                        raise ValueError(f'{table}: unexpected output header')
                    for row in reader:
                        copy.write_row([row[c] or None for c in columns + ['SOURCE_ROW']])
                        count += 1
                if count != manifest['tables'][table]['accepted']:
                    raise ValueError(f'{table}: row count mismatch')
                if sha256(directory / f'{table}.csv') != manifest['outputs'][table]:
                    raise ValueError(f'{table}: artifact changed during load')
            conn.execute('DELETE FROM insights.person_links')
            conn.execute('DELETE FROM insights.payments')
            conn.execute('DELETE FROM insights.companies')
            conn.execute('DELETE FROM insights.company_entities')
            conn.execute('''INSERT INTO insights.company_entities
                SELECT synthetic_u FROM stage_companies UNION SELECT synthetic_u FROM stage_person_links
                UNION SELECT payer FROM stage_payments UNION SELECT beneficiary FROM stage_payments''')
            for table in SOURCES:
                conn.execute(sql.SQL('INSERT INTO insights.{} SELECT * FROM {}').format(
                    sql.Identifier(table), sql.Identifier('stage_' + table)))
            conn.execute('''INSERT INTO insights.csv_snapshot(singleton,manifest) VALUES(true,%s)
                ON CONFLICT(singleton) DO UPDATE SET manifest=excluded.manifest,loaded_at=now()''', (Jsonb(manifest),))
            return {'status': 'loaded', 'tables': {t: manifest['tables'][t]['accepted'] for t in SOURCES}}

    def save_document(self, doc, document_id, fingerprint):
        """Upsert one prepared document atomically; False when the stored artifact is identical."""
        with connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(521102)')
            existing = conn.execute('SELECT artifact_sha256 FROM insights.documents WHERE document_id=%s',
                                    (document_id,)).fetchone()
            if existing and existing[0] == fingerprint:
                return False
            conn.execute('''INSERT INTO insights.documents
                (document_id,source_file,title,sha256,published_at,date_source,page_count,extraction_settings,artifact_sha256,
                 source_type,source_url,publisher,external_id,published_at_timestamp,source_metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(document_id) DO UPDATE SET
                source_file=excluded.source_file,title=excluded.title,sha256=excluded.sha256,published_at=excluded.published_at,
                date_source=excluded.date_source,page_count=excluded.page_count,
                extraction_settings=excluded.extraction_settings,artifact_sha256=excluded.artifact_sha256,loaded_at=now(),
                source_type=excluded.source_type,source_url=excluded.source_url,publisher=excluded.publisher,
                external_id=excluded.external_id,published_at_timestamp=excluded.published_at_timestamp,source_metadata=excluded.source_metadata''',
                (document_id, doc['source_file'], doc['title'], doc['sha256'], doc.get('published_at'),
                 doc.get('date_source'), len(doc['pages']), Jsonb(doc['settings']), fingerprint,
                 doc.get('source_type', 'pdf'), doc.get('source_url'), doc.get('publisher', 'Альфа-Банк'),
                 doc.get('external_id'), doc.get('published_at_timestamp'), Jsonb(doc.get('source_metadata', {}))))
            def chunk_id(page_number, index, text):
                return hashlib.sha256(f'{document_id}:{page_number}:{index}:{text}'.encode()).hexdigest()
            kept_ids = [chunk_id(p['page_number'], i, c['text']) for p in doc['pages'] for i,c in enumerate(p['chunks'])]
            # Metadata/tag changes keep unchanged chunk IDs and downstream embeddings intact.
            conn.execute('DELETE FROM insights.document_chunks WHERE document_id=%s AND NOT (chunk_id=ANY(%s))', (document_id, kept_ids))
            conn.execute('DELETE FROM insights.document_pages WHERE document_id=%s AND page_number>%s', (document_id, len(doc['pages'])))
            for page in doc['pages']:
                conn.execute('''INSERT INTO insights.document_pages VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(document_id,page_number) DO UPDATE SET text=excluded.text,method=excluded.method,
                    confidence=excluded.confidence,needs_review=excluded.needs_review''',
                             (document_id, page['page_number'], page['text'], page['method'],
                              page['confidence'], page['needs_review']))
                for index, chunk in enumerate(page['chunks']):
                    factors = chunk.get('factors', {})
                    metadata = {k: chunk[k] for k in ('sector', 'countries', 'regions', 'country_mentions', 'unmapped_countries') if k in chunk}
                    metadata['classifier'] = doc.get('tagging')
                    conn.execute('''INSERT INTO insights.document_chunks
                        (chunk_id,document_id,page_number,chunk_index,start_char,end_char,text,tags,
                         key_rate,fx,commodities,consumer_demand,construction_market,tagging_version,tag_metadata)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(chunk_id) DO UPDATE SET start_char=excluded.start_char,end_char=excluded.end_char,
                        tags=excluded.tags,key_rate=excluded.key_rate,fx=excluded.fx,commodities=excluded.commodities,
                        consumer_demand=excluded.consumer_demand,construction_market=excluded.construction_market,
                        tagging_version=excluded.tagging_version,tag_metadata=excluded.tag_metadata''',
                        (chunk_id(page['page_number'], index, chunk['text']), document_id, page['page_number'], index,
                         chunk['start_char'], chunk['end_char'], chunk['text'], chunk.get('tags', []),
                         *[bool(factors.get(k, 0)) for k in FACTORS], chunk.get('tagging_version'), Jsonb(metadata)))
            return True

    def search(self, query, limit=5, tags=None):
        with connect() as conn:
            rows = conn.execute('''SELECT chunk_id,source_file,page_number,published_at,text,source_url,tags,
                ts_rank_cd(search_vector,websearch_to_tsquery('russian',%s)) AS score
                FROM insights.rag_chunks WHERE search_vector @@ websearch_to_tsquery('russian',%s)
                AND tags @> %s::text[]
                ORDER BY score DESC,chunk_id LIMIT %s''', (query, query, tags or [], limit))
            return [dict(zip(['chunk_id', 'source_file', 'page_number', 'published_at', 'text', 'source_url', 'tags', 'score'], row)) for row in rows]

    def import_news_snapshot(self, root, fingerprint, rows, report, audit):
        names = sql.SQL(',').join(map(sql.Identifier, FIELDS))
        with connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(521103)')
            conn.execute("SET LOCAL lock_timeout = '10s'")
            conn.execute("SET LOCAL statement_timeout = '60s'")
            conn.execute('CREATE TEMP TABLE stage_news (LIKE insights.news_articles) ON COMMIT DROP')
            with conn.cursor().copy(sql.SQL('COPY stage_news ({}) FROM STDIN').format(names)) as copy:
                for row in rows:
                    copy.write_row(with_json(row))
            conditions = sql.SQL(' OR ').join(
                sql.SQL('n.{} IS DISTINCT FROM s.{}').format(sql.Identifier(k), sql.Identifier(k)) for k in IMMUTABLE)
            conflict = conn.execute(sql.SQL('''SELECT s.news_id FROM stage_news s
                JOIN insights.news_articles n USING(news_id) WHERE {} LIMIT 1''').format(conditions)).fetchone()
            if conflict:
                raise ValueError('Existing article content differs; import rolled back: ' + conflict[0])
            before = conn.execute('SELECT count(*) FROM insights.news_articles').fetchone()[0]
            conn.execute('''INSERT INTO insights.news_imports
                (import_sha256,source_path,row_count,report,audit) VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT(import_sha256) DO NOTHING''',
                         (fingerprint, str(root), len(rows), Jsonb(report), Jsonb(audit)))
            # Unique URL and text hash constraints reject conflicting IDs atomically.
            conn.execute(sql.SQL('''INSERT INTO insights.news_articles ({}) SELECT {} FROM stage_news
                ON CONFLICT(news_id) DO NOTHING''').format(names, names))
            conn.execute('''INSERT INTO insights.news_import_items(import_sha256,news_id)
                SELECT %s,news_id FROM stage_news ON CONFLICT DO NOTHING''', (fingerprint,))
            loaded = conn.execute('SELECT count(*) FROM insights.news_import_items WHERE import_sha256=%s',
                                  (fingerprint,)).fetchone()[0]
            after = conn.execute('SELECT count(*) FROM insights.news_articles').fetchone()[0]
            if loaded != len(rows):
                raise ValueError('Imported membership count mismatch')
            return dict(status='loaded' if after > before else 'unchanged', snapshot_records=loaded,
                        added=after-before, total=after, import_sha256=fingerprint,
                        collection_target_met=report['target_met'])

    def import_news_briefs(self, source, fingerprint, rows, report):
        """Add new headlines/excerpts; a brief never replaces an article or an earlier brief."""
        names = sql.SQL(',').join(map(sql.Identifier, BRIEF_FIELDS))
        record = BRIEF_FIELDS.index('source_record')
        with connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(521103)')
            conn.execute("SET LOCAL statement_timeout = '120s'")
            conn.execute('''INSERT INTO insights.news_imports
                (import_sha256,source_path,row_count,report,audit) VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT(import_sha256) DO NOTHING''',
                         (fingerprint, str(source), max(len(rows), 1), Jsonb(report),
                          Jsonb(dict(verified=False, reason='collector briefs have no source receipts'))))
            conn.execute('CREATE TEMP TABLE stage_briefs (LIKE insights.news_briefs) ON COMMIT DROP')
            with conn.cursor().copy(sql.SQL('COPY stage_briefs ({}) FROM STDIN').format(names)) as copy:
                for row in rows:
                    values = list(row)
                    values[record] = Jsonb(values[record])
                    copy.write_row(values)
            before = conn.execute('SELECT count(*) FROM insights.news_briefs').fetchone()[0]
            # A full article with the same ID or URL already carries the content.
            conn.execute(sql.SQL('''INSERT INTO insights.news_briefs ({}) SELECT {} FROM stage_briefs s
                WHERE NOT EXISTS (SELECT 1 FROM insights.news_articles a WHERE a.news_id = s.news_id OR a.url = s.url)
                ON CONFLICT DO NOTHING''').format(names, names))
            after = conn.execute('SELECT count(*) FROM insights.news_briefs').fetchone()[0]
            return dict(status='loaded' if after > before else 'unchanged', records=len(rows),
                        added=after - before, total=after)
