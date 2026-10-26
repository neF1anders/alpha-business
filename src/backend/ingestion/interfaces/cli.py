"""Command line interface: python -m ingestion <command>."""
import argparse
import json
from pathlib import Path
from collections import Counter

from ..application.extract_pdfs import extract_pdfs
from ..application.load_documents import initialize, load_csv, load_news, load_pdfs, search
from ..application.load_news_snapshot import load_collector_jsonl, load_news_snapshot
from ..application.prepare_csv import prepare_csv
from ..application.prepare_news import prepare_news
from ..application.tag_artifacts import tag_artifacts
from ..infrastructure.csv_staging import SqliteStaging
from ..infrastructure.pdf_reader import PyMuPdfReader
from ..infrastructure.tag_cache import SqliteTagCache
from ..infrastructure.tesseract import TesseractOcr
from .composition import create_storage, create_tagger


def news_format(source, requested='auto'):
    if requested != 'auto':return requested
    root=Path(source)
    if root.is_file():return 'jsonl'
    chunk_manifest=(root/'manifest.json').is_file()
    snapshot=(root/'report.json').is_file() and (root/'news.jsonl').is_file()
    if chunk_manifest == snapshot:
        raise ValueError('Cannot determine news format; use --format chunks or --format snapshot')
    return 'chunks' if chunk_manifest else 'snapshot'


def main():
    parser = argparse.ArgumentParser(description='Prepare CSV/PDF sources and load the insights PostgreSQL database.')
    commands = parser.add_subparsers(dest='command', required=True)
    clean = commands.add_parser('prepare-csv', help='Filter raw CSV files; write clean CSV, audit and manifest')
    clean.add_argument('--source', default='data/raw/users')
    clean.add_argument('--output', default='data/processed/csv')
    pdf = commands.add_parser('extract-pdfs', help='Extract/OCR PDFs into page and chunk artifacts')
    pdf.add_argument('--source', default='data/raw/reports')
    pdf.add_argument('--output', default='data/processed/pdf')
    pdf.add_argument('--metadata', default=None)
    pdf.add_argument('--tesseract', default='tesseract')
    pdf.add_argument('--languages', default='rus+eng')
    pdf.add_argument('--dpi', type=int, default=250)
    pdf.add_argument('--psm', type=int, choices=range(14), default=3)
    pdf.add_argument('--timeout', type=int, default=180)
    pdf.add_argument('--chunk-size', type=int, default=1400)
    pdf.add_argument('--overlap', type=int, default=200)
    pdf.add_argument('--min-confidence', type=float, default=60)
    pdf.add_argument('--force-ocr', action='store_true')
    news = commands.add_parser('prepare-news', help='Prepare JSON/JSONL news articles')
    news.add_argument('--source', default='data/raw/reports/news.jsonl')
    news.add_argument('--output', default='data/processed/news')
    news.add_argument('--chunk-size', type=int, default=1400)
    news.add_argument('--overlap', type=int, default=200)
    news.add_argument('--include-partial', action='store_true',
                      help='Include excerpts and title-only articles in retrieval; preserve original quality metadata')
    news.add_argument('--dry-run', action='store_true', help='Validate and count articles/chunks without writing artifacts')
    tag = commands.add_parser('tag-chunks', help='Tag all prepared PDF and news chunks')
    tag.add_argument('--pdf-source', default='data/processed/pdf')
    tag.add_argument('--news-only', action='store_true', help='Tag only news; do not read PDF artifacts')
    tag.add_argument('--news-source', default='data/processed/news')
    tag.add_argument('--output', default='data/processed/tagged')
    tag.add_argument('--companies', default='data/processed/csv/companies.csv')
    tag.add_argument('--model', default='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
    tag.add_argument('--revision', default=None)
    tag.add_argument('--threshold', type=float, default=0.30)
    tag.add_argument('--margin', type=float, default=0.03)
    tag.add_argument('--batch-size', type=int, default=32)
    tag.add_argument('--cache-dir', default='data/cache')
    commands.add_parser('init-db', help='Create the insights schema and indexes')
    for name, directory in [('load-csv', 'csv'), ('load-pdfs', 'tagged/pdf')]:
        load = commands.add_parser(name)
        load.add_argument('--source', default=f'data/processed/{directory}')
    news = commands.add_parser('load-news', help='Load a prepared chunk manifest, an audited news snapshot or full articles from a collector JSONL')
    news.add_argument('--source', default='data/processed/tagged/news')
    news.add_argument('--format', choices=['auto','chunks','snapshot','jsonl'], default='auto')
    news.add_argument('--include-partial', action='store_true',
                      help='jsonl: also load headlines and excerpts into news_briefs (shown as briefs)')
    find = commands.add_parser('search', help='Smoke-test full-text retrieval')
    find.add_argument('query')
    find.add_argument('--limit', type=int, default=5)
    find.add_argument('--tag', action='append', default=[], help='Require a tag (repeatable, AND)')
    args = parser.parse_args()
    storage = create_storage()
    if args.command == 'prepare-csv':
        result = prepare_csv(storage, SqliteStaging(), args.source, args.output)
    elif args.command == 'prepare-news':
        result = prepare_news(storage, args.source, args.output, args.chunk_size, args.overlap,
                              include_partial=args.include_partial, dry_run=args.dry_run)
    elif args.command == 'tag-chunks':
        result = tag_artifacts(storage, SqliteTagCache(), None if args.news_only else args.pdf_source, args.news_source, args.output,
                               args.companies, model=args.model, revision=args.revision,
                               threshold=args.threshold, margin=args.margin, cache_dir=args.cache_dir,
                               batch_size=args.batch_size, tagger_factory=create_tagger)
    elif args.command == 'extract-pdfs':
        result = extract_pdfs(storage, PyMuPdfReader(), TesseractOcr(), args.source, args.output,
                              metadata_path=args.metadata,
                              tesseract=args.tesseract, languages=args.languages, dpi=args.dpi,
                              psm=args.psm, timeout=args.timeout, size=args.chunk_size,
                              overlap=args.overlap, min_confidence=args.min_confidence,
                              force_ocr=args.force_ocr)
    else:
        from ..infrastructure.postgres import PostgresDocumentStore
        store = PostgresDocumentStore()
        if args.command == 'init-db':
            initialize(store)
            result = {'status': 'schema_ready'}
        elif args.command == 'load-csv':
            result = load_csv(storage, store, args.source)
        elif args.command == 'load-pdfs':
            result = load_pdfs(storage, store, args.source)
        elif args.command == 'load-news':
            format_ = news_format(args.source, args.format)
            if format_ == 'snapshot':
                result = load_news_snapshot(store, args.source)
            elif format_ == 'jsonl':
                result = load_collector_jsonl(store, args.source, include_partial=args.include_partial)
            else:
                loaded = load_news(storage, store, args.source)
                result = {'documents': len(loaded), 'statuses': dict(Counter(item['status'] for item in loaded))}
        else:
            if args.limit < 1 or args.limit > 100:
                parser.error('--limit must be between 1 and 100')
            result = search(store, args.query, args.limit, args.tag)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
