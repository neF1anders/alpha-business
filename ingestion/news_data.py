"""Collect a bounded RSS news snapshot with source receipts; no LLM calls.

Run: python -m ingestion.news_data --output data/processed/news/pilot-001
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import xml.etree.ElementTree as ET

FEEDS = (
    ('cbr_press', 'Банк России', 'primary', 'https://cbr.ru/rss/RssPress'),
    ('kommersant_economics', 'Коммерсантъ', 'media', 'https://www.kommersant.ru/RSS/section-economics.xml'),
    ('kommersant_business', 'Коммерсантъ', 'media', 'https://www.kommersant.ru/RSS/section-business.xml'),
)
EXTENDED_FEEDS = (
    ('cbr_events', 'Банк России', 'primary', 'https://cbr.ru/rss/eventrss'),
    ('kommersant_money', 'Коммерсантъ', 'media', 'https://www.kommersant.ru/RSS/money.xml'),
    ('kommersant_telecom', 'Коммерсантъ', 'media', 'https://www.kommersant.ru/RSS/section-telecom.xml'),
)
HOSTS = {'cbr.ru', 'www.cbr.ru', 'kommersant.ru', 'www.kommersant.ru'}
USER_AGENT = 'AlphaBusinessHackathon/0.1 (bounded research collector)'
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
BLOCKS = {'p', 'div', 'section', 'li', 'h1', 'h2', 'h3', 'h4', 'tr', 'blockquote', 'br'}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def canonical_url(url):
    parts = urlsplit(url.strip())
    if parts.scheme not in ('http', 'https') or parts.hostname not in HOSTS or parts.username or parts.password or parts.port not in (None, 80, 443):
        raise ValueError('URL is outside configured public sources')
    host = 'www.kommersant.ru' if 'kommersant' in parts.hostname else 'www.cbr.ru'
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('from', 'ref', 'fbclid', 'gclid')]
    return urlunsplit(('https', host, parts.path or '/', urlencode(sorted(query)), ''))


class RestrictedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        canonical_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, limit=8_000_000):
    canonical_url(url)
    req = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
    with build_opener(RestrictedRedirect()).open(req, timeout=20) as response:
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('response exceeds byte limit')
        return raw, dict(url=response.url, status=response.status,
                         content_type=response.headers.get('Content-Type'), fetched_at=now(), sha256=digest(raw))


class TextParser(HTMLParser):
    """Extract visible selected elements, preserving paragraph boundaries."""
    def __init__(self, source=None):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.stack = []
        self.parts = []
        self.access_restricted = False
        self.selected_blocks = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set((a.get('class') or '').split())
        hidden = tag in ('script', 'style', 'noscript', 'nav', 'footer', 'button') or 'hidden' in a or a.get('aria-hidden') == 'true'
        marker = ' '.join([a.get('class') or '', a.get('id') or '']).lower()
        if any(word in marker for word in ('paywall', 'subscription-wall', 'paid-content')):
            self.access_restricted = True
            hidden = True
        selected = self.source is None
        if self.source == 'cbr':
            selected = 'landing-text' in classes
        elif self.source == 'kommersant':
            selected = bool(classes & {'doc-text-block__paragraph', 'article__text'})
        parent_selected = self.stack[-1][1] if self.stack else False
        parent_hidden = self.stack[-1][2] if self.stack else False
        active, skip = selected or parent_selected, hidden or parent_hidden
        if selected and not parent_selected:
            self.selected_blocks += 1
        if active and not skip and tag in BLOCKS:
            self.parts.append('\n')
        if tag not in VOID:
            self.stack.append((tag, active, skip))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                if self.stack[i][1] and not self.stack[i][2] and tag in BLOCKS:
                    self.parts.append('\n')
                del self.stack[i:]
                break

    def handle_data(self, data):
        active = self.stack[-1][1] if self.stack else self.source is None
        hidden = self.stack[-1][2] if self.stack else False
        if active and not hidden:
            self.parts.append(data)

    def text(self):
        lines = [re.sub(r'\s+', ' ', line).strip() for line in ''.join(self.parts).splitlines()]
        return '\n\n'.join(line for line in lines if line)


def plain(html):
    parser = TextParser()
    parser.feed(html)
    return parser.text()


def published_date(value):
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def parse_feed(raw, feed_id, source_name, source_kind, feed_url, fetched_at):
    root = ET.fromstring(raw)
    records, errors = [], []
    for index, item in enumerate(root.findall('./channel/item')):
        try:
            url = canonical_url(item.findtext('link') or '')
            title = plain(item.findtext('title') or '')
            if not title:
                raise ValueError('empty title')
            published_raw = item.findtext('pubDate')
            records.append(dict(news_id='news:' + digest(url)[:24], url=url,
                title=title, source=source_name, source_kind=source_kind,
                feed_ids=[feed_id], feed_urls=[feed_url], categories=[x.text for x in item.findall('category') if x.text],
                published_at=published_date(published_raw), published_at_raw=published_raw,
                date_source='rss_pubDate', event_at=None, fetched_at=fetched_at,
                rss_description=plain(item.findtext('description') or ''), event_id=None))
        except ValueError as exc:
            errors.append(dict(stage='feed_item', feed_id=feed_id, item_index=index, error=str(exc)))
    return records, errors


def merge_urls(records):
    unique = {}
    for record in records:
        if record['url'] not in unique:
            unique[record['url']] = record
        else:
            previous = unique[record['url']]
            for field in ('feed_ids', 'feed_urls', 'categories'):
                previous[field] = sorted(set(previous[field] + record[field]))
    return list(unique.values())


def extract_article(html, source):
    parser = TextParser(source)
    parser.feed(html)
    # JSON-LD access flags can signal a partial article even with visible paragraphs.
    restricted = parser.access_restricted or bool(re.search(r'"isAccessibleForFree"\s*:\s*(?:false|"false")', html, re.I))
    return parser.text(), restricted, parser.selected_blocks


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n' for row in rows), encoding='utf-8')


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


def collect(output, max_per_feed=100, delay=0.5, extended=False, reuse_from=None):
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
            raw, receipt = download(url)
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
                raw, receipt = download(record['url'])
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
    write_jsonl(output / 'news.jsonl', records)
    write_jsonl(output / 'errors.jsonl', errors)
    write_jsonl(output / 'receipts.jsonl', receipts)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--max-per-feed', type=int, default=100)
    parser.add_argument('--delay', type=float, default=0.5)
    parser.add_argument('--extended', action='store_true', help='Add CBR events, money and telecom feeds')
    parser.add_argument('--reuse-from', help='Verified prior snapshot: reuse HTML with original timestamps')
    args = parser.parse_args()
    report = collect(args.output, args.max_per_feed, args.delay, args.extended, args.reuse_from)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['retrieval_eligible']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
