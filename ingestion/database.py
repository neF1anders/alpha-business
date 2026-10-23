import csv
import hashlib
import json
import os
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from .common import sha256
from .csv_data import SOURCES

SCHEMA = Path(__file__).resolve().parent.parent / 'sql' / 'schema.sql'


def connect():
    # Either DATABASE_URL or standard libpq PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD.
    return psycopg.connect(os.environ.get('DATABASE_URL', ''), connect_timeout=10)


def initialize():
    with connect() as conn:
        conn.execute(SCHEMA.read_text(encoding='utf-8'))


def load_csv(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['version'] != 1:
        raise ValueError('Unsupported CSV artifact version')
    for table in SOURCES:
        if sha256(directory / f'{table}.csv') != manifest['outputs'][table]:
            raise ValueError(f'{table}: artifact checksum mismatch; rerun prepare-csv')
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


def validate_document(doc):
    if doc.get('version') != 1 or not doc['pages']:
        raise ValueError('Invalid document artifact')
    if [p['page_number'] for p in doc['pages']] != list(range(1, len(doc['pages']) + 1)):
        raise ValueError('Missing or duplicate PDF pages')
    for page in doc['pages']:
        for chunk in page['chunks']:
            start, end = chunk['start_char'], chunk['end_char']
            if not 0 <= start < end <= len(page['text']) or page['text'][start:end] != chunk['text']:
                raise ValueError('Chunk does not match source page')


def load_pdfs(directory):
    paths = sorted(Path(directory).glob('*.pdf.json'))
    if not paths:
        raise ValueError(f'No complete PDF artifacts in {directory}; run extract-pdfs')
    results = []
    for path in paths:
        raw = path.read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        doc = json.loads(raw)
        validate_document(doc)
        document_id = hashlib.sha256(doc['source_file'].encode()).hexdigest()
        with connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(521102)')
            existing = conn.execute('SELECT artifact_sha256 FROM insights.documents WHERE document_id=%s',
                                    (document_id,)).fetchone()
            if existing and existing[0] == fingerprint:
                results.append({'file': doc['source_file'], 'status': 'unchanged'})
                continue
            conn.execute('''INSERT INTO insights.documents
                (document_id,source_file,title,sha256,published_at,date_source,page_count,extraction_settings,artifact_sha256)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(document_id) DO UPDATE SET
                title=excluded.title,sha256=excluded.sha256,published_at=excluded.published_at,
                date_source=excluded.date_source,page_count=excluded.page_count,
                extraction_settings=excluded.extraction_settings,artifact_sha256=excluded.artifact_sha256,loaded_at=now()''',
                (document_id, doc['source_file'], doc['title'], doc['sha256'], doc.get('published_at'),
                 doc.get('date_source'), len(doc['pages']), Jsonb(doc['settings']), fingerprint))
            # Cascades to chunks (and downstream embedding tables should use ON DELETE CASCADE).
            conn.execute('DELETE FROM insights.document_pages WHERE document_id=%s', (document_id,))
            for page in doc['pages']:
                conn.execute('''INSERT INTO insights.document_pages VALUES (%s,%s,%s,%s,%s,%s)''',
                             (document_id, page['page_number'], page['text'], page['method'],
                              page['confidence'], page['needs_review']))
                for index, chunk in enumerate(page['chunks']):
                    chunk_id = hashlib.sha256(f'{document_id}:{page["page_number"]}:{index}:{chunk["text"]}'.encode()).hexdigest()
                    conn.execute('''INSERT INTO insights.document_chunks
                        (chunk_id,document_id,page_number,chunk_index,start_char,end_char,text)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                        (chunk_id, document_id, page['page_number'], index,
                         chunk['start_char'], chunk['end_char'], chunk['text']))
            results.append({'file': doc['source_file'], 'status': 'loaded', 'pages': len(doc['pages'])})
    return results


def search(query, limit=5):
    with connect() as conn:
        rows = conn.execute('''SELECT chunk_id,source_file,page_number,published_at,text,
            ts_rank_cd(search_vector,websearch_to_tsquery('russian',%s)) AS score
            FROM insights.rag_chunks WHERE search_vector @@ websearch_to_tsquery('russian',%s)
            ORDER BY score DESC,chunk_id LIMIT %s''', (query, query, limit))
        return [dict(zip(['chunk_id', 'source_file', 'page_number', 'published_at', 'text', 'score'], row)) for row in rows]
