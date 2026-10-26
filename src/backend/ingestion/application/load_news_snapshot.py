"""Load an audited news snapshot atomically; never replace existing source text."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..domain.news_snapshot import FIELDS, aware
from ..domain.news_urls import canonical_url, digest


def article_values(row, fingerprint):
    """Validate one eligible collector record and return its column values."""
    if row.get('retrieval_eligible') is not True or row.get('quality_flags'):
        raise ValueError('Ineligible article in snapshot')
    for name in ('news_id', 'url', 'title', 'text', 'source', 'source_kind', 'text_sha256'):
        if not isinstance(row.get(name), str) or not row[name].strip() or '\x00' in row[name]:
            raise ValueError(f'Invalid article field: {name}')
    if row['source_kind'] not in ('primary', 'media', 'bank'):
        raise ValueError('Unknown source kind')
    if len(row['text']) < 200 or digest(re.sub(r'\s+', ' ', row['text']).strip()) != row['text_sha256']:
        raise ValueError('Article text checksum/length mismatch')
    values = dict(row)
    values['published_at'] = aware(row['published_at'], 'published_at')
    values['event_at'] = aware(row.get('event_at'), 'event_at', optional=True)
    values['fetched_at'] = aware(row.get('article_fetched_at') or row.get('fetched_at'), 'fetched_at')
    if values['published_at'] > datetime.now(timezone.utc):
        raise ValueError('Future publication')
    categories = row.get('categories', [])
    if not isinstance(categories, list) or any(not isinstance(c, str) for c in categories):
        raise ValueError('Invalid categories')
    values.update(categories=categories, source_record=row, first_import_sha256=fingerprint)
    return values


def reject_duplicates(rows):
    seen = {k: set() for k in ('news_id', 'url', 'text_sha256')}
    for row in rows:
        for key in seen:
            if row[key] in seen[key]:
                raise ValueError(f'Duplicate {key} in snapshot')
            seen[key].add(row[key])


def read_snapshot(directory):
    root = Path(directory).resolve()
    raw = (root / 'news.jsonl').read_bytes()
    fingerprint = digest(raw)
    report = json.loads((root / 'report.json').read_text())
    audit = json.loads((root / 'audit.json').read_text())
    if (report.get('news_sha256') != fingerprint or audit.get('news_sha256') != fingerprint
            or audit.get('verified') is not True):
        raise ValueError('Snapshot checksum/audit mismatch; run news_audit first')
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not rows or len(rows) != report['eligible'] or len(rows) != audit['records']:
        raise ValueError('Snapshot row count mismatch')
    receipts = {r['news_id']: r for r in
                (json.loads(line) for line in (root / 'receipts.jsonl').read_text().splitlines() if line.strip())
                if r['kind'] == 'article'}
    prepared = []
    for row in rows:
        values = article_values(row, fingerprint)
        url = canonical_url(row['url'])
        if url != row['url'] or row['news_id'] != 'news:' + digest(url)[:24]:
            raise ValueError('Article ID/canonical URL mismatch')
        receipt = receipts.get(row['news_id'])
        if not receipt:
            raise ValueError('Missing source receipt')
        source_path = (root / receipt['path']).resolve()
        if not source_path.is_relative_to(root) or not source_path.is_file():
            raise ValueError('Invalid source receipt path')
        values['raw_receipt'] = receipt
        prepared.append(tuple(values[name] for name in FIELDS))
    reject_duplicates(rows)
    return root, fingerprint, prepared, report, audit


def read_collector_jsonl(path):
    """Read full articles from a collector JSONL (news.jsonl, news_combined.jsonl) without source receipts.

    Excerpts and title-only records are skipped: news_articles holds full texts only.
    The import is recorded as unverified, so it is never mistaken for an audited snapshot.
    """
    path = Path(path).resolve()
    raw = path.read_bytes()
    fingerprint = digest(raw)
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    eligible = [row for row in rows if row.get('retrieval_eligible') is True]
    if not eligible:
        raise ValueError('No retrieval-eligible articles in collector file')
    receipt = dict(kind='collector_jsonl', verified=False, source_file=path.name)
    prepared = []
    for row in eligible:
        values = article_values(row, fingerprint)
        values['raw_receipt'] = receipt
        prepared.append(tuple(values[name] for name in FIELDS))
    reject_duplicates(eligible)
    report = dict(format='collector_jsonl', input=len(rows), eligible=len(eligible),
                  skipped_partial=len(rows) - len(eligible), target_met=None)
    audit = dict(verified=False, reason='collector jsonl has no source receipts', records=len(eligible))
    return path, fingerprint, prepared, report, audit


def load_news_snapshot(store, directory):
    root, fingerprint, rows, report, audit = read_snapshot(directory)
    return store.import_news_snapshot(root, fingerprint, rows, report, audit)


def load_collector_jsonl(store, path):
    result = store.import_news_snapshot(*read_collector_jsonl(path))
    result.pop('collection_target_met')
    return result
