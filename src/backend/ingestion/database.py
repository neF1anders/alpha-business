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
from .taxonomy import FACTORS, SECTORS

SCHEMA = Path(__file__).resolve().parents[3] / 'sql' / 'schema.sql'


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
            if 'tagging_version' in chunk:
                tags = chunk.get('tags')
                factors = chunk.get('factors', {})
                if not isinstance(tags, list) or any(not isinstance(tag, str) or ':' not in tag for tag in tags):
                    raise ValueError('Invalid chunk tags')
                if set(factors) != set(FACTORS) or any(type(v) is not int or v not in (0, 1) for v in factors.values()):
                    raise ValueError('Invalid factor flags')
                if {tag for tag in tags if tag.startswith('factor:')} != {'factor:' + k for k,v in factors.items() if v}:
                    raise ValueError('Factor flags and tags disagree')
                for tag in tags:
                    namespace, value = tag.split(':', 1)
                    if namespace not in {'factor', 'sector', 'country', 'region'} or not value:
                        raise ValueError('Unknown tag namespace or empty tag')
                    if namespace == 'sector' and value not in SECTORS:
                        raise ValueError('Unknown sector')


def load_pdfs(directory):
    directory = Path(directory)
    paths = sorted(directory.glob('*.pdf.json'))
    if (directory / 'manifest.json').exists():
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        paths = []
        for name, digest in manifest['outputs'].items():
            if Path(name).name != name or not name.endswith('.pdf.json') or sha256(directory / name) != digest:
                raise ValueError('Invalid tagged PDF manifest or checksum')
            paths.append(directory / name)
    if not paths:
        raise ValueError(f'No complete PDF artifacts in {directory}; run extract-pdfs')
    return load_documents(paths)


def load_news(directory):
    from .news_documents import artifact_paths
    paths = artifact_paths(directory)
    if not paths:
        raise ValueError('No prepared news articles')
    return load_documents(paths)


def load_documents(paths):
    results = []
    for number, path in enumerate(paths, 1):
        raw = path.read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        doc = json.loads(raw)
        validate_document(doc)
        document_id = doc.get('document_id') or hashlib.sha256(doc['source_file'].encode()).hexdigest()
        with connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(521102)')
            existing = conn.execute('SELECT artifact_sha256 FROM insights.documents WHERE document_id=%s',
                                    (document_id,)).fetchone()
            if existing and existing[0] == fingerprint:
                results.append({'file': doc['source_file'], 'status': 'unchanged'})
                continue
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
            results.append({'file': doc['source_file'], 'status': 'loaded', 'pages': len(doc['pages'])})
        if number % 250 == 0:
            print(f'Loaded {number}/{len(paths)} documents', flush=True)
    return results


def search(query, limit=5, tags=None):
    with connect() as conn:
        rows = conn.execute('''SELECT chunk_id,source_file,page_number,published_at,text,source_url,tags,
            ts_rank_cd(search_vector,websearch_to_tsquery('russian',%s)) AS score
            FROM insights.rag_chunks WHERE search_vector @@ websearch_to_tsquery('russian',%s)
            AND tags @> %s::text[]
            ORDER BY score DESC,chunk_id LIMIT %s''', (query, query, tags or [], limit))
        return [dict(zip(['chunk_id', 'source_file', 'page_number', 'published_at', 'text', 'source_url', 'tags', 'score'], row)) for row in rows]
