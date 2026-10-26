"""Resumable, bounded collection from dated business archives.

State and compressed originals live in a separate snapshot directory. No LLM.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError

from ..domain.clock import now
from ..domain.news_feeds import RUBRICS
from ..domain.news_html import article_metadata, ArchiveParser, extract_article
from ..domain.news_urls import canonical_url, digest

# The crawl freezes the sources it ran with next to its output; the audit compares them.
COLLECTOR_SOURCE = Path(__file__)
EXTRACTOR_SOURCE = Path(__file__).parents[1] / 'domain' / 'news_html.py'


class Fetcher:
    """Global rate limiter in front of an HTTP fetcher shared by worker threads."""
    def __init__(self, http, interval):
        self.http = http
        self.interval = interval
        self.lock = threading.Lock()
        self.next_time = 0.0

    def get(self, url):
        with self.lock:
            scheduled = max(time.monotonic(), self.next_time)
            self.next_time = scheduled + self.interval
        time.sleep(max(0, scheduled - time.monotonic()))
        return self.http.download(url)


def save_raw(storage, root, key, raw, receipt, kind):
    relative = Path('raw') / (key + ('.html.gz' if kind != 'rss' else '.xml.gz'))
    storage.write_gzip(root / relative, raw)
    return dict(receipt, path=str(relative), compression='gzip', kind=kind)


def seed_snapshot(storage, db, root, seed):
    if db.execute("SELECT 1 FROM settings WHERE key='seed_loaded'").fetchone():
        return
    receipts = {r['news_id']: r for r in
                (json.loads(line) for line in (seed / 'receipts.jsonl').read_text().splitlines())
                if r['kind'] == 'article'}
    for line in (seed / 'news.jsonl').read_text().splitlines():
        row = json.loads(line)
        receipt = receipts.get(row['news_id'])
        saved = None
        if receipt:
            path = (seed / receipt['path']).resolve()
            if not path.is_relative_to(seed.resolve()):
                raise ValueError('seed receipt path escapes directory')
            raw = path.read_bytes()
            if receipt.get('compression') == 'gzip':
                raw = gzip.decompress(raw)
            elif receipt.get('compression') not in (None, 'none'):
                raise ValueError('unsupported seed compression')
            if digest(raw) != receipt['sha256']:
                raise ValueError('seed SHA256 mismatch')
            saved = save_raw(storage, root, row['news_id'].split(':')[1], raw,
                             dict(receipt, reused_from=str(seed.resolve())), 'article')
        row['seed_snapshot'] = str(seed.resolve())
        db.execute('INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?,?,?)',
                   (row['news_id'], row['url'], json.dumps(row, ensure_ascii=False), 'done',
                    int(row['retrieval_eligible']), row['text_sha256'],
                    json.dumps(saved) if saved else None, None))
    db.execute("INSERT INTO settings VALUES ('seed_loaded', '1')")
    db.commit()


def add_archive(storage, db, root, fetcher, url, rubric, day):
    if db.execute('SELECT 1 FROM archive_pages WHERE url=?', (url,)).fetchone():
        return
    raw, receipt = fetcher.get(url)
    parser = ArchiveParser()
    parser.feed(raw.decode('utf-8-sig', errors='replace'))
    saved = save_raw(storage, root, 'archive-' + digest(url)[:24], raw, receipt, 'archive')
    for item in parser.items:
        news_id = 'news:' + digest(item['url'])[:24]
        old = db.execute('SELECT record FROM articles WHERE news_id=?', (news_id,)).fetchone()
        if old:
            row = json.loads(old[0])
        else:
            row = dict(news_id=news_id, url=item['url'], title=item['title'], source='Коммерсантъ',
                       source_kind='media', event_at=None, event_id=None, categories=[],
                       archive_urls=[], rubric_ids=[], archive_dates=[], fetched_at=receipt['fetched_at'])
        for key, values in [('archive_urls', [url]), ('archive_dates', [day.isoformat()]),
                            ('rubric_ids', item['rubric_ids']), ('categories', [RUBRICS[rubric]])]:
            row[key] = sorted(set(row.get(key, []) + values))
        payload = json.dumps(row, ensure_ascii=False)
        db.execute('INSERT INTO articles(news_id,url,record) VALUES (?,?,?) '
                   'ON CONFLICT(news_id) DO UPDATE SET record=excluded.record', (news_id,item['url'],payload))
    db.execute('INSERT INTO archive_pages VALUES (?,?,?)', (url, json.dumps(saved),len(parser.items)))
    db.commit()


def process_article(storage, row, root, fetcher, cutoff):
    try:
        raw, receipt = fetcher.get(row['url'])
        saved = save_raw(storage, root, row['news_id'].split(':')[1], raw, receipt, 'article')
        html = raw.decode('utf-8-sig', errors='replace')
        metadata = article_metadata(html)
        text, restricted, blocks = extract_article(html, 'kommersant')
        flags = []
        if not metadata['published_at']:
            flags.append('missing_publication_date')
        elif datetime.fromisoformat(metadata['published_at']) > cutoff:
            flags.append('future_publication_date')
        if len(text) < 200:
            flags.append('short_or_missing_article_body')
        if restricted:
            flags.append('access_restricted')
        if metadata['canonical']:
            try:
                if canonical_url(metadata['canonical']) != row['url']:
                    flags.append('canonical_url_mismatch')
            except ValueError:
                flags.append('canonical_url_mismatch')
        if re.search(r'новость\s+аннулирована', text, re.I):
            flags.append('withdrawn')
        row.update(title=metadata['title'] or row['title'],
                   published_at=metadata['published_at'], published_at_raw=metadata['published_at_raw'],
                   date_source='html_article_published_time', text=text, text_chars=len(text),
                   text_sha256=digest(re.sub(r'\s+', ' ', text).strip()),
                   fetched_at=receipt['fetched_at'], article_fetched_at=receipt['fetched_at'],
                   content_origin='article_html', extraction_blocks=blocks,
                   content_status='partial_access' if restricted else ('article_body_extracted' if len(text)>=200 else 'short_or_missing_article_body'),
                   quality_flags=flags, retrieval_eligible=not flags, duplicate_of=None)
        return row, saved, None
    except Exception as exc:
        error = dict(type=type(exc).__name__, message=str(exc),
                     http_status=exc.code if isinstance(exc, HTTPError) else None)
        row.update(retrieval_eligible=False, quality_flags=['fetch_failed'], content_status='fetch_failed')
        return row, None, error


def persist(db, row, receipt, error):
    sha = row.get('text_sha256')
    if row['retrieval_eligible'] and sha:
        duplicate = db.execute('SELECT news_id FROM articles WHERE text_sha=? AND eligible=1 AND news_id!=? LIMIT 1',
                               (sha,row['news_id'])).fetchone()
        if duplicate:
            row['duplicate_of'] = duplicate[0]
            row['quality_flags'].append('exact_text_duplicate')
            row['retrieval_eligible'] = False
    db.execute('UPDATE articles SET record=?, status=?, eligible=?, text_sha=?, receipt=?,error=? WHERE news_id=?',
               (json.dumps(row,ensure_ascii=False), 'failed' if error else 'done', int(row['retrieval_eligible']),sha,
                json.dumps(receipt) if receipt else None, json.dumps(error) if error else None, row['news_id']))
    db.commit()


def counts(db):
    return dict(candidates=db.execute('SELECT count(*) FROM articles').fetchone()[0],
                processed=db.execute("SELECT count(*) FROM articles WHERE status!='pending'").fetchone()[0],
                eligible=db.execute('SELECT count(*) FROM articles WHERE eligible=1').fetchone()[0],
                failed=db.execute("SELECT count(*) FROM articles WHERE status='failed'").fetchone()[0],
                archive_pages=db.execute('SELECT count(*) FROM archive_pages').fetchone()[0])


def checkpoint(db, root, target, stage):
    report = dict(counts(db), target=target, stage=stage, updated_at=now())
    path = root / 'progress.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    temp.replace(path)
    print(json.dumps(report,ensure_ascii=False),flush=True)


def export(storage, db, root, target, started, stop_reason):
    rows = [json.loads(r[0]) for r in db.execute("SELECT record FROM articles WHERE status!='pending' ORDER BY news_id")]
    eligible = [r for r in rows if r['retrieval_eligible']]
    receipts = [dict(json.loads(r[1]), news_id=r[0]) for r in db.execute('SELECT news_id,receipt FROM articles WHERE receipt IS NOT NULL')]
    receipts.extend(json.loads(r[0]) for r in db.execute('SELECT receipt FROM archive_pages'))
    errors = [dict(news_id=r[0],**json.loads(r[1])) for r in db.execute('SELECT news_id,error FROM articles WHERE error IS NOT NULL')]
    for filename,data in [('all_records.jsonl',rows),('news.jsonl',eligible),('receipts.jsonl',receipts),('errors.jsonl',errors)]:
        temp=root/(filename+'.tmp');storage.write_jsonl(temp,data);temp.replace(root/filename)
    dates=[r['published_at'] for r in eligible]
    report=dict(counts(db),target=target,target_met=len(eligible)>=target,
                started_at=started,completed_at=now(),stop_reason=stop_reason,
                by_source=dict(Counter(r['source'] for r in eligible)),
                by_status=dict(Counter(r['content_status'] for r in rows)),
                by_category=dict(Counter(c for r in eligible for c in r.get('categories',[]))),
                date_from=min(dates) if dates else None,date_to=max(dates) if dates else None,
                news_sha256=digest((root/'news.jsonl').read_bytes()),
                collector_sha256=digest((root/'collector.py').read_bytes()),
                notes=['news.jsonl contains only eligible unique-text records; all_records.jsonl also retains excluded records',
                       'Publication eligibility is technical extraction quality, not company relevance',
                       'Same-event articles are not yet clustered',
                       'Dates use article publication metadata for archives and original RSS dates for seed records'])
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return report


def run(storage, http, open_state, args):
    root=Path(args.output);root.mkdir(parents=True,exist_ok=True);(root/'raw').mkdir(exist_ok=True)
    db=open_state(root)
    settings=dict(since=args.since.isoformat(),until=args.until.isoformat(),seed=str(Path(args.seed).resolve()),rubrics=RUBRICS)
    previous=db.execute("SELECT value FROM settings WHERE key='configuration'").fetchone()
    if previous and json.loads(previous[0])!=settings:
        raise ValueError('resume configuration differs from existing snapshot')
    if not previous:
        db.execute("INSERT INTO settings VALUES ('configuration',?)",(json.dumps(settings),));db.commit()
        (root/'collector.py').write_bytes(COLLECTOR_SOURCE.read_bytes())
        (root/'news_data.py').write_bytes(EXTRACTOR_SOURCE.read_bytes())
    elif (root/'collector.py').read_bytes()!=COLLECTOR_SOURCE.read_bytes():
        raise ValueError('collector changed; use a new snapshot or the frozen collector')
    started=now();cutoff=min(datetime.now(timezone.utc),datetime.combine(args.until+timedelta(days=1),datetime.min.time(),timezone.utc))
    seed_snapshot(storage,db,root,Path(args.seed))
    fetcher=Fetcher(http,args.interval)
    day=args.until;last_progress=0.0;stop_reason='archive_range_exhausted'
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        while counts(db)['eligible']<args.target:
            pending=db.execute("SELECT count(*) FROM articles WHERE status='pending'").fetchone()[0]
            if pending<50 and day>=args.since:
                for rubric in RUBRICS:
                    url=f'https://www.kommersant.ru/archive/rubric/{rubric}/day/{day.isoformat()}'
                    try:
                        add_archive(storage,db,root,fetcher,url,rubric,day)
                    except HTTPError as exc:
                        # Do not continue hammering a host that throttles or denies access.
                        if exc.code in (401,403,429):
                            stop_reason=f'archive_http_{exc.code}'
                            checkpoint(db,root,args.target,stop_reason)
                            return export(storage,db,root,args.target,started,stop_reason)
                        print(json.dumps(dict(archive_error=url,error=str(exc))),flush=True)
                    except Exception as exc:
                        print(json.dumps(dict(archive_error=url,error=str(exc))),flush=True)
                day-=timedelta(days=1)
                if time.monotonic()-last_progress>20:
                    checkpoint(db,root,args.target,'discovering');last_progress=time.monotonic()
                continue
            batch=[json.loads(r[0]) for r in db.execute("SELECT record FROM articles WHERE status='pending' ORDER BY rowid LIMIT ?",(min(40,args.target-counts(db)['eligible']),))]
            if not batch:
                if day<args.since:break
                continue
            stop=False
            for row,receipt,error in pool.map(lambda r:process_article(storage,r,root,fetcher,cutoff),batch):
                persist(db,row,receipt,error)
                if error and error.get('http_status') in (401,403,429):
                    stop=True;stop_reason='article_http_'+str(error['http_status'])
            if time.monotonic()-last_progress>20:
                checkpoint(db,root,args.target,'collecting');last_progress=time.monotonic()
            if stop:break
        if counts(db)['eligible']>=args.target:stop_reason='target_reached'
    return export(storage,db,root,args.target,started,stop_reason)
