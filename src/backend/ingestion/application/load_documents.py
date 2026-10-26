"""Load prepared CSV snapshots and PDF/news document artifacts into PostgreSQL."""
import hashlib
import json
from pathlib import Path

from ..domain.csv_rules import SOURCES
from ..domain.document_rules import validate_document
from .prepare_news import artifact_paths


def initialize(store):
    store.initialize()


def load_csv(storage, store, directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['version'] != 1:
        raise ValueError('Unsupported CSV artifact version')
    for table in SOURCES:
        if storage.sha256(directory / f'{table}.csv') != manifest['outputs'][table]:
            raise ValueError(f'{table}: artifact checksum mismatch; rerun prepare-csv')
    return store.replace_csv_snapshot(directory, manifest)


def load_pdfs(storage, store, directory):
    directory = Path(directory)
    paths = sorted(directory.glob('*.pdf.json'))
    if (directory / 'manifest.json').exists():
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        paths = []
        for name, digest in manifest['outputs'].items():
            if Path(name).name != name or not name.endswith('.pdf.json') or storage.sha256(directory / name) != digest:
                raise ValueError('Invalid tagged PDF manifest or checksum')
            paths.append(directory / name)
    if not paths:
        raise ValueError(f'No complete PDF artifacts in {directory}; run extract-pdfs')
    return load_documents(store, paths)


def load_news(storage, store, directory):
    paths = artifact_paths(storage, directory)
    if not paths:
        raise ValueError('No prepared news articles')
    return load_documents(store, paths)


def load_documents(store, paths):
    results = []
    for number, path in enumerate(paths, 1):
        raw = path.read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        doc = json.loads(raw)
        validate_document(doc)
        document_id = doc.get('document_id') or hashlib.sha256(doc['source_file'].encode()).hexdigest()
        if not store.save_document(doc, document_id, fingerprint):
            results.append({'file': doc['source_file'], 'status': 'unchanged'})
            continue
        results.append({'file': doc['source_file'], 'status': 'loaded', 'pages': len(doc['pages'])})
        if number % 250 == 0:
            print(f'Loaded {number}/{len(paths)} documents', flush=True)
    return results


def search(store, query, limit=5, tags=None):
    return store.search(query, limit, tags)
