import argparse
import json
import sys

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
    commands.add_parser('init-db', help='Create the insights schema and indexes')
    for name, directory in [('load-csv', 'csv'), ('load-pdfs', 'pdf')]:
        load = commands.add_parser(name)
        load.add_argument('--source', default=f'data/processed/{directory}')
    find = commands.add_parser('search', help='Smoke-test full-text retrieval')
    find.add_argument('query')
    find.add_argument('--limit', type=int, default=5)
    args = parser.parse_args()
    if args.command == 'prepare-csv':
        result = prepare_csv(args.source, args.output)
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
        else:
            if args.limit < 1 or args.limit > 100:
                parser.error('--limit must be between 1 and 100')
            result = database.search(args.query, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
