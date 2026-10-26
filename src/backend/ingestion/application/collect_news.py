"""Collect a bounded RSS news snapshot with source receipts; no LLM calls."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import time

from ..domain.clock import now
from ..domain.news_feeds import EXTENDED_FEEDS, FEEDS, merge_urls, parse_feed
from ..domain.news_html import extract_article
from ..domain.news_urls import digest


def load_article_cache(snapshot):
    """Verify cached receipts before reuse, preserving original fetch timestamps."""
    if snapshot is None:
        return {}
    root = Path(snapshot).resolve()
    cache = {}
    for line in (root / 'receipts.jsonl').read_text(encoding='utf-8').splitlines():
        receipt = json.loads(line)
        if receipt['kind'] != 'article':
            continue
        path = (root / receipt['path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('cached receipt path leaves snapshot directory')
        raw = path.read_bytes()
        if digest(raw) != receipt['sha256']:
            raise ValueError('cached article SHA256 mismatch')
        cache[receipt['news_id']] = (raw, receipt)
    return cache


def collect(storage, fetcher, output, max_per_feed=100, delay=0.5, extended=False, reuse_from=None):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('output must be empty; use a new snapshot directory')
    if not 1 <= max_per_feed <= 500 or delay < 0.25:
        raise ValueError('max_per_feed must be 1..500 and delay at least 0.25 seconds')
    cache = load_article_cache(reuse_from)
    cache_hits = 0
    raw_dir = output / 'raw'
    raw_dir.mkdir(parents=True, exist_ok=True)
    started_at = now()
    records, errors, receipts, feed_counts = [], [], [], {}
    for feed_id, name, kind, url in FEEDS + (EXTENDED_FEEDS if extended else ()):
        try:
            raw, receipt = fetcher.download(url)
            path = raw_dir / (feed_id + '.xml')
            path.write_bytes(raw)
            receipts.append(dict(receipt, path=str(path.relative_to(output)), kind='rss'))
            items, rejected = parse_feed(raw, feed_id, name, kind, url, receipt['fetched_at'])
            feed_counts[feed_id] = dict(available=len(items), selected=min(len(items), max_per_feed))
            records.extend(items[:max_per_feed])
            errors.extend(rejected)
        except Exception as exc:
            errors.append(dict(stage='feed', feed_id=feed_id, error=f'{type(exc).__name__}: {exc}'))
        time.sleep(delay)
    total_feed_items = len(records)
    records = merge_urls(records)
    cutoff = datetime.fromisoformat(started_at)
    for index, record in enumerate(records):
        record.update(text=record['rss_description'], content_status='rss_only',
                      content_origin='rss_description', article_fetched_at=None,
                      retrieval_eligible=False, extraction_blocks=0)
        try:
            if record['news_id'] in cache:
                raw, cached_receipt = cache[record['news_id']]
                receipt = dict(cached_receipt, reused_from=str(Path(reuse_from).resolve()))
                cache_hits += 1
            else:
                time.sleep(delay)
                raw, receipt = fetcher.download(record['url'])
            path = raw_dir / (record['news_id'].split(':')[1] + '.html')
            path.write_bytes(raw)
            receipts.append(dict(receipt, path=str(path.relative_to(output)), kind='article', news_id=record['news_id']))
            record['article_fetched_at'] = receipt['fetched_at']
            source = 'cbr' if record['source_kind'] == 'primary' else 'kommersant'
            text, restricted, blocks = extract_article(raw.decode('utf-8-sig', errors='replace'), source)
            record['extraction_blocks'] = blocks
            if len(text) >= 200:
                record.update(text=text, content_origin='article_html',
                              content_status='partial_access' if restricted else 'article_body_extracted')
            elif restricted:
                record['content_status'] = 'partial_access'
            else:
                record['content_status'] = 'rss_only_no_article_body'
        except Exception as exc:
            record['content_status'] = 'rss_only_fetch_failed'
            errors.append(dict(stage='article', news_id=record['news_id'], url=record['url'], error=f'{type(exc).__name__}: {exc}'))
        record['text_sha256'] = digest(re.sub(r'\s+', ' ', record['text']).strip())
        record['text_chars'] = len(record['text'])
        record['quality_flags'] = []
        if not record['published_at']:
            record['quality_flags'].append('missing_publication_date')
        elif datetime.fromisoformat(record['published_at']) > cutoff:
            record['quality_flags'].append('future_publication_date')
        if record['content_status'] != 'article_body_extracted':
            record['quality_flags'].append('article_completeness_not_established')
        record['retrieval_eligible'] = not record['quality_flags']
        if (index + 1) % 10 == 0:
            print(json.dumps(dict(processed=index + 1, total=len(records)), ensure_ascii=False), flush=True)
    # Exact-text duplicates are flagged, not destroyed; event-level grouping is a separate step.
    hashes = {}
    for record in sorted(records, key=lambda r: (not r['retrieval_eligible'], r['news_id'])):
        sha = record['text_sha256']
        record['duplicate_of'] = hashes.get(sha) if record['text'] else None
        if record['duplicate_of']:
            record['quality_flags'].append('exact_text_duplicate')
            record['retrieval_eligible'] = False
        elif record['text']:
            hashes[sha] = record['news_id']
    records.sort(key=lambda r: r['news_id'])
    storage.write_jsonl(output / 'news.jsonl', records)
    storage.write_jsonl(output / 'errors.jsonl', errors)
    storage.write_jsonl(output / 'receipts.jsonl', receipts)
    report = dict(started_at=started_at, completed_at=now(), feeds=feed_counts,
                  feed_items=total_feed_items, unique_urls=len(records),
                  url_duplicates_merged=total_feed_items-len(records),
                  by_source=dict(Counter(r['source'] for r in records)),
                  by_status=dict(Counter(r['content_status'] for r in records)),
                  retrieval_eligible=sum(r['retrieval_eligible'] for r in records),
                  exact_text_duplicates=sum(bool(r['duplicate_of']) for r in records),
                  errors=len(errors), code_sha256=digest(Path(__file__).read_bytes()),
                  news_sha256=digest((output / 'news.jsonl').read_bytes()),
                  max_per_feed=max_per_feed, delay_seconds=delay, extended=extended,
                  cached_articles_reused=cache_hits, reuse_from=str(reuse_from) if reuse_from else None,
                  notes=['article_body_extracted means visible selected HTML blocks; completeness needs sampling',
                         'No semantic event clustering, company ranking, or PostgreSQL import yet',
                         'Raw snapshots and article bodies are local artifacts, not automatically published'])
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report
