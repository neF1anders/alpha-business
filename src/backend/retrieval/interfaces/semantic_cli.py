"""Pinned local E5 embeddings and resumable PostgreSQL index; no inference API.

Only the explicit download command contacts Hugging Face. All encoding is offline.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json

from ..application.semantic_index import SemanticNewsIndex, build_index
from ..domain.embedding import MODEL
from ..infrastructure.e5_encoder import LocalE5, download_model
from ..infrastructure.postgres_activity import PostgresActivityCatalog
from ..infrastructure.postgres_embeddings import PostgresEmbeddingStore


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['download','index','search'])
    parser.add_argument('--device',choices=['auto','cpu','mps'],default='auto')
    parser.add_argument('--company-id')
    parser.add_argument('--as-of')
    parser.add_argument('--limit',type=int,default=5)
    args=parser.parse_args()
    if args.command=='download':
        print(download_model())
    elif args.command=='index':
        print(json.dumps(build_index(LocalE5(device=args.device),PostgresEmbeddingStore())))
    else:
        if not args.company_id:parser.error('--company-id required for search')
        as_of=datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc)
        if as_of.utcoffset() is None:parser.error('Timezone-aware as-of required')
        if not 1<=args.limit<=100:parser.error('--limit must be 1..100')
        activity=PostgresActivityCatalog().activity_of(args.company_id)
        if not activity:parser.error('Company activity unavailable')
        query='Основная деятельность компании: '+activity
        encoder=LocalE5(device=args.device);index=SemanticNewsIndex(PostgresEmbeddingStore())
        hits=index.search(encoder.query(query),as_of=as_of,published_after=as_of-timedelta(days=90),limit=args.limit)
        print(json.dumps(dict(company_id=args.company_id,query=query,model=MODEL,
            interpretation='semantic candidates, not business impact judgments',
            hits=[dict(**hit,title=index.documents[hit['news_id']]['title'],url=index.documents[hit['news_id']]['url'])
                  for hit in hits]),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
