"""Library entry point for bank report chunk search."""
from ..application.document_search import search_documents
from ..infrastructure.postgres_documents import PostgresDocumentStore


def retrieve(query, *, as_of, limit=5, published_after=None, source_files=None,
             include_undated=False, max_per_document=None, query_mode="websearch"):
    """Source-linked bank report chunks; see application.document_search for the rules."""
    return search_documents(PostgresDocumentStore(), query, as_of=as_of, limit=limit,
                            published_after=published_after, source_files=source_files,
                            include_undated=include_undated, max_per_document=max_per_document,
                            query_mode=query_mode)
