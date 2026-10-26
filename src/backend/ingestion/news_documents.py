"""Convert collected JSON/JSONL articles to the same document artifacts as PDFs."""
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .common import sha256, write_json
from .pdf_data import chunks

# These flags describe incomplete content, explicitly allowed by --include-partial.
# Other quality flags (e.g. future dates) still require review.
PARTIAL_STATUSES = {'short_excerpt', 'metadata_only'}
PARTIAL_FLAGS = {'article_not_fetched', 'not_full_article', 'title_only',
                 'short_excerpt_not_full_article', 'excerpt_truncated',
                 'publication_time_minute_precision'}


def records(path):
    with path.open(encoding='utf-8-sig') as stream:
        if path.suffix.lower() == '.jsonl':
            for number, line in enumerate(stream, 1):
                if line.strip():
                    try:
                        yield number, json.loads(line), None
                    except json.JSONDecodeError:
                        yield number, None, 'invalid_json'
        else:
            data = json.load(stream)
            if isinstance(data, dict) and 'news' in data:
                data = data['news']
            if not isinstance(data, list):
                raise ValueError('JSON must contain an array of articles or {"news": [...]}')
            for number, item in enumerate(data, 1):
                yield number, item, None


def prepare_news(source, output, size=1400, overlap=200, *, include_partial=False, dry_run=False):
    source, output = Path(source), Path(output)
    list(chunks('test', size, overlap))
    manifest = output / 'manifest.json'
    source_hash = sha256(source)
    if not dry_run:
        output.mkdir(parents=True, exist_ok=True)
        manifest.unlink(missing_ok=True)
    stats = Counter()
    content_counts = Counter()
    pending, conflicts, audit = {}, set(), []
    for row_number, raw, error in records(source):
        stats['input'] += 1
        reason = error
        if not reason and not isinstance(raw, dict):
            reason = 'not_an_object'
        partial = not reason and include_partial and raw.get('content_status') in PARTIAL_STATUSES
        if not reason:
            for field in ('news_id', 'title', 'url') if partial else ('news_id', 'title', 'text', 'url'):
                if not isinstance(raw.get(field), str) or not raw[field].strip():
                    reason = f'missing_{field}'
                    break
        if not reason and partial and raw.get('text') is not None and not isinstance(raw['text'], str):
            reason = 'invalid_text'
        if not reason and (not isinstance(raw.get('quality_flags', []), list)
                           or any(not isinstance(flag, str) for flag in raw.get('quality_flags', []))):
            reason = 'invalid_quality_flags'
        if not reason and (urlparse(raw['url']).scheme not in {'http', 'https'} or not urlparse(raw['url']).netloc):
            reason = 'invalid_url'
        if not reason and raw.get('duplicate_of'):
            reason = 'source_duplicate'
        if not reason and raw.get('retrieval_eligible', True) is not True and not partial:
            reason = 'source_not_eligible'
        timestamp = None
        if not reason and raw.get('published_at'):
            try:
                timestamp = datetime.fromisoformat(raw['published_at'].replace('Z', '+00:00'))
            except (ValueError, TypeError, AttributeError):
                reason = 'invalid_date'
        if reason:
            audit.append(dict(row=row_number, news_id=raw.get('news_id') if isinstance(raw, dict) else None, reason=reason))
            continue
        key = raw['news_id'].strip()
        item = (row_number, raw, timestamp)
        if key in pending:
            if pending[key][1] == raw:
                audit.append(dict(row=row_number, news_id=key, reason='duplicate_record'))
            else:
                conflicts.add(key)
                audit.append(dict(row=row_number, news_id=key, reason='conflicting_news_id'))
        else:
            pending[key] = item
    outputs, seen_text, seen_urls = {}, set(), set()
    for key, (row_number, raw, timestamp) in pending.items():
        if key in conflicts:
            audit.append(dict(row=row_number, news_id=key, reason='conflicting_news_id'))
            continue
        partial = include_partial and raw.get('content_status') in PARTIAL_STATUSES
        text = (raw.get('text') or '').replace('\x00', '').replace('\r\n', '\n').strip()
        content_kind = 'full_text'
        if partial:
            title = raw['title'].replace('\x00', '').strip()
            content_kind = 'excerpt' if text and text != title else 'title_only'
            text = title + '\n\n' + text if content_kind == 'excerpt' else title
        review_flags = set(raw.get('quality_flags', []))
        if partial:
            review_flags -= PARTIAL_FLAGS
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest in seen_text or raw['url'] in seen_urls:
            audit.append(dict(row=row_number, news_id=key, reason='duplicate_text_or_url'))
            continue
        seen_text.add(digest)
        seen_urls.add(raw['url'])
        # Preserve collector metadata, but do not trust its precomputed hash/length.
        metadata = {k: v for k, v in raw.items() if k != 'text'}
        metadata.update(input_file=source.name, input_row=row_number)
        metadata['ingestion'] = dict(content_kind=content_kind,
                                     partial_retrieval_allowed=bool(partial),
                                     review_flags=sorted(review_flags))
        document_id = hashlib.sha256(('news:' + key).encode()).hexdigest()
        doc = dict(version=1, document_id=document_id, source_type='news',
                   source_file=source.name + '#' + key, external_id=key,
                   source_url=raw['url'], publisher=raw.get('source'), source_metadata=metadata,
                   title=raw['title'], sha256=digest,
                   published_at=timestamp.date().isoformat() if timestamp else None,
                   published_at_timestamp=timestamp.isoformat() if timestamp and timestamp.tzinfo else None,
                   date_source=raw.get('date_source'),
                   settings=dict(version=2, extractor='json-news', size=size, overlap=overlap,
                                 include_partial=include_partial),
                   pages=[dict(page_number=1, text=text, method='json', confidence=None,
                               needs_review=bool(review_flags),
                               chunks=list(chunks(text, size, overlap)))])
        if not doc['pages'][0]['chunks']:
            audit.append(dict(row=row_number, news_id=key, reason='empty_text'))
            continue
        filename = document_id + '.news.json'
        if not dry_run:
            write_json(output / filename, doc)
            outputs[filename] = sha256(output / filename)
        stats['accepted'] += 1
        stats['chunks'] += len(doc['pages'][0]['chunks'])
        stats['needs_review'] += int(bool(review_flags))
        content_counts[content_kind] += 1
    stats['rejected'] = len(audit)
    if not dry_run:
        with (output / 'rejected.jsonl').open('w', encoding='utf-8') as stream:
            for item in audit:
                stream.write(json.dumps(item, ensure_ascii=False) + '\n')
    if sha256(source) != source_hash:
        raise RuntimeError('News source changed during processing')
    if not stats['accepted'] and not dry_run:
        raise ValueError('No eligible articles; see rejected.jsonl')
    report = dict(version=1, source_file=source.name, source_sha256=source_hash,
                  counts=dict(stats), content_kinds=dict(content_counts), include_partial=include_partial,
                  rejection_reasons=dict(Counter(r['reason'] for r in audit)), outputs=outputs)
    if dry_run:
        report['dry_run'] = True
    else:
        write_json(manifest, report)
    return {k: v for k, v in report.items() if k != 'outputs'}


def artifact_paths(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != 1:
        raise ValueError('Unsupported news manifest')
    result = []
    for name, digest in manifest['outputs'].items():
        if Path(name).name != name or not name.endswith('.news.json'):
            raise ValueError('Invalid artifact filename')
        path = directory / name
        if sha256(path) != digest:
            raise ValueError(f'News artifact checksum mismatch: {name}')
        result.append(path)
    return result
