import argparse
import json
import sys
from collections import Counter

from .csv_data import prepare_csv
from .pdf_data import extract_pdfs


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
    tag = commands.add_parser('tag-chunks', help='Tag all prepared PDF and news chunks')
    tag.add_argument('--pdf-source', default='data/processed/pdf')
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
    for name, directory in [('load-csv', 'csv'), ('load-pdfs', 'tagged/pdf'), ('load-news', 'tagged/news')]:
        load = commands.add_parser(name)
        load.add_argument('--source', default=f'data/processed/{directory}')
    find = commands.add_parser('search', help='Smoke-test full-text retrieval')
    find.add_argument('query')
    find.add_argument('--limit', type=int, default=5)
    find.add_argument('--tag', action='append', default=[], help='Require a tag (repeatable, AND)')
    args = parser.parse_args()
    if args.command == 'prepare-csv':
        result = prepare_csv(args.source, args.output)
    elif args.command == 'prepare-news':
        from .news_documents import prepare_news
        result = prepare_news(args.source, args.output, args.chunk_size, args.overlap)
    elif args.command == 'tag-chunks':
        from .tagging import tag_artifacts
        result = tag_artifacts(args.pdf_source, args.news_source, args.output, args.companies,
                               model=args.model, revision=args.revision, threshold=args.threshold,
                               margin=args.margin, cache_dir=args.cache_dir, batch_size=args.batch_size)
    elif args.command == 'extract-pdfs':
        result = extract_pdfs(args.source, args.output, metadata_path=args.metadata,
                              tesseract=args.tesseract, languages=args.languages, dpi=args.dpi,
                              psm=args.psm, timeout=args.timeout, size=args.chunk_size,
                              overlap=args.overlap, min_confidence=args.min_confidence,
                              force_ocr=args.force_ocr)
    else:
        from . import database
        if args.command == 'init-db':
            database.initialize()
            result = {'status': 'schema_ready'}
        elif args.command == 'load-csv':
            result = database.load_csv(args.source)
        elif args.command == 'load-pdfs':
            result = database.load_pdfs(args.source)
        elif args.command == 'load-news':
            loaded = database.load_news(args.source)
            result = {'documents': len(loaded), 'statuses': dict(Counter(item['status'] for item in loaded))}
        else:
            if args.limit < 1 or args.limit > 100:
                parser.error('--limit must be between 1 and 100')
            result = database.search(args.query, args.limit, args.tag)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
