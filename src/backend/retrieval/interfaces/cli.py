"""CLI for evidence retrieval and producing an offline evaluation run."""
import argparse
from datetime import date
import json
from pathlib import Path
import time

from evaluation.metrics import read_jsonl
from .library import retrieve


def search_record(record, limit, per_document=None, query_mode="websearch"):
    started = time.perf_counter()
    results = retrieve(record['query'], as_of=date.fromisoformat(record['as_of']), limit=limit,
                       published_after=date.fromisoformat(record['published_after']) if record.get('published_after') else None,
                       source_files=record.get('source_files'),
                       include_undated=record.get('include_undated', False),
                       max_per_document=per_document, query_mode=query_mode)
    return dict(query=record['query'], as_of=record['as_of'], method='postgres_fts_' + query_mode,
                latency_ms=(time.perf_counter() - started) * 1000, results=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    search = commands.add_parser('search')
    search.add_argument('query')
    search.add_argument('--as-of', type=date.fromisoformat, required=True)
    search.add_argument('--published-after', type=date.fromisoformat)
    search.add_argument('--source-file', action='append')
    search.add_argument('--include-undated', action='store_true')
    run = commands.add_parser('run')
    run.add_argument('--queries', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True)
    for command in [search, run]:
        command.add_argument('--query-mode', choices=['websearch', 'natural'], default='websearch')
        command.add_argument('--top-k', type=int, default=5)
        command.add_argument('--max-per-document', type=int)
    args = parser.parse_args()
    try:
        if args.command == 'search':
            record = dict(query=args.query, as_of=args.as_of.isoformat(),
                          published_after=args.published_after.isoformat() if args.published_after else None,
                          source_files=args.source_file, include_undated=args.include_undated)
            print(json.dumps(search_record(record, args.top_k, args.max_per_document, args.query_mode),
                             ensure_ascii=False, indent=2, default=str, allow_nan=False))
        else:
            if args.output.resolve() == args.queries.resolve():
                raise ValueError('output must differ from queries')
            records = read_jsonl(args.queries)
            seen, output = set(), []
            for record in records:
                query_id = record['query_id']
                if not isinstance(query_id, str) or not query_id.strip() or query_id in seen:
                    raise ValueError('query IDs must be nonempty and unique')
                seen.add(query_id)
                result = search_record(record, args.top_k, args.max_per_document, args.query_mode)
                output.append(dict(query_id=query_id, ranking=[r['chunk_id'] for r in result['results']],
                                   latency_ms=result['latency_ms'], query=result['query'], as_of=result['as_of'],
                                   method=result['method'], include_undated=record.get('include_undated', False),
                                   published_after=record.get('published_after'), source_files=record.get('source_files'),
                                   top_k=args.top_k, max_per_document=args.max_per_document))
            if not records:
                raise ValueError('queries must not be empty')
            args.output.parent.mkdir(parents=True, exist_ok=True)
            rendered = ''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n' for row in output)
            temporary = args.output.with_suffix(args.output.suffix + '.tmp')
            temporary.write_text(rendered, encoding='utf-8')
            temporary.replace(args.output)
            print(json.dumps(dict(queries=len(output), output=str(args.output)), ensure_ascii=False))
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
