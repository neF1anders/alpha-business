"""Verify a completed news snapshot against its stored source files."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import json
import re
from pathlib import Path
import statistics

from .news_data import digest, extract_article
from .news_archive import article_metadata


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def audit(snapshot, seed=None, allow_partial=False):
    root=Path(snapshot).resolve()
    report=json.loads((root/'report.json').read_text())
    if digest((root/'collector.py').read_bytes())!=report['collector_sha256']:
        raise ValueError('frozen collector SHA256 mismatch')
    if (root/'news_data.py').read_bytes()!=Path(__file__).with_name('news_data.py').read_bytes():
        raise ValueError('audit extractor differs from frozen collector dependency')
    rows=read_jsonl(root/'news.jsonl')
    receipts=read_jsonl(root/'receipts.jsonl')
    ids=[r['news_id'] for r in rows];urls=[r['url'] for r in rows]
    if len(ids)!=len(set(ids)) or len(urls)!=len(set(urls)):
        raise ValueError('duplicate news IDs or URLs')
    if digest((root/'news.jsonl').read_bytes())!=report['news_sha256']:
        raise ValueError('news.jsonl SHA256 mismatch')
    if len(rows)!=report['eligible']:
        raise ValueError('physical rows differ from declared count')
    target_met = len(rows) >= report['target']
    if report['target_met'] != target_met:
        raise ValueError('declared target status differs from physical count')
    if not target_met and not allow_partial:
        raise ValueError('physical rows do not meet declared target')
    if len({r['text_sha256'] for r in rows})!=len(rows):
        raise ValueError('exact text duplicates in eligible corpus')
    by_id={r['news_id']:r for r in receipts if r['kind']=='article'}
    raw_bytes=0
    for receipt in receipts:
        path=(root/receipt['path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('receipt path leaves snapshot')
        raw=path.read_bytes()
        if receipt.get('compression')=='gzip':raw=gzip.decompress(raw)
        if digest(raw)!=receipt['sha256']:raise ValueError('raw SHA256 mismatch: '+receipt['path'])
        raw_bytes+=len(raw)
    for row in rows:
        if digest(re.sub(r'\s+', ' ', row['text']).strip())!=row['text_sha256']:
            raise ValueError('text SHA256 mismatch: '+row['news_id'])
        if not row['retrieval_eligible'] or row['quality_flags'] or len(row['text'])<200:
            raise ValueError('ineligible row in eligible corpus: '+row['news_id'])
        if datetime.fromisoformat(row['published_at'])>datetime.now(timezone.utc):
            raise ValueError('future publication')
        receipt=by_id[row['news_id']]
        raw=(root/receipt['path']).read_bytes()
        if receipt.get('compression')=='gzip':raw=gzip.decompress(raw)
        html=raw.decode('utf-8-sig',errors='replace')
        text,restricted,blocks=extract_article(html,'cbr' if row['source_kind']=='primary' else 'kommersant')
        if text!=row['text'] or restricted or blocks!=row['extraction_blocks']:
            raise ValueError('stored text does not reproduce: '+row['news_id'])
        if row['date_source']=='html_article_published_time':
            if article_metadata(html)['published_at']!=row['published_at']:
                raise ValueError('article date does not reproduce: '+row['news_id'])
    seed_ids=set()
    if seed:
        seed_ids={r['news_id'] for r in read_jsonl(Path(seed)/'news.jsonl') if r['retrieval_eligible']}
        if not seed_ids.issubset(ids):raise ValueError('eligible seed records lost')
    result=dict(verified=True,target_met=target_met,requested_target=report['target'],records=len(rows),unique_ids=len(set(ids)),unique_urls=len(set(urls)),
                unique_texts=len({r['text_sha256'] for r in rows}),source_files_verified=len(receipts),
                raw_bytes_verified=raw_bytes,eligible_seed_records_preserved=len(seed_ids),
                new_eligible_records=len(rows)-len(seed_ids),
                by_source=dict(Counter(r['source'] for r in rows)),
                date_from=min(r['published_at'] for r in rows),date_to=max(r['published_at'] for r in rows),
                median_text_chars=statistics.median(len(r['text']) for r in rows),
                news_sha256=report['news_sha256'],
                limitations=['Does not prove factual correctness or company relevance',
                             'Different articles about the same event may remain',
                             'Visible article body is reproducible, but completeness is not guaranteed'])
    (root/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',required=True);parser.add_argument('--seed')
    parser.add_argument('--allow-partial', action='store_true', help='Verify saved rows without claiming the requested target was reached')
    args=parser.parse_args()
    print(json.dumps(audit(args.snapshot,args.seed,args.allow_partial),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
