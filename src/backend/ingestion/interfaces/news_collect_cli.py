"""Collect a bounded RSS news snapshot with source receipts; no LLM calls.

Run: python -m ingestion.news_data --output data/processed/news/pilot-001
"""
import argparse
import json

from ..application.collect_news import collect
from ..infrastructure.http_fetcher import UrllibFetcher
from .composition import create_storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--max-per-feed', type=int, default=100)
    parser.add_argument('--delay', type=float, default=0.5)
    parser.add_argument('--extended', action='store_true', help='Add CBR events, money and telecom feeds')
    parser.add_argument('--reuse-from', help='Verified prior snapshot: reuse HTML with original timestamps')
    args = parser.parse_args()
    report = collect(create_storage(), UrllibFetcher(), args.output, args.max_per_feed, args.delay, args.extended, args.reuse_from)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['retrieval_eligible']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
