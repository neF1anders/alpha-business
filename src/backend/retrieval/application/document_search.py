"""Source-linked bank report chunks for a text query (lexical, read-only)."""
from datetime import date

from ..domain.query import natural_query
from .ports import DocumentSearchStore


def search_documents(store: DocumentSearchStore, query, *, as_of, limit=5, published_after=None,
                     source_files=None, include_undated=False, max_per_document=None,
                     query_mode="websearch"):
    """Return source-linked chunks, not news or generated company-specific claims.

    No future reports or low-quality OCR pages are returned. Unknown publication
    dates require an explicit opt-in; date eligibility does not establish whether
    an older forecast has been superseded. Optional document caps diversify the
    results and should be evaluated against the uncapped baseline.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError('query must be a nonempty string of at most 2000 characters')
    if query_mode not in {'websearch', 'natural'}:
        raise ValueError('query_mode must be websearch or natural')
    if type(as_of) is not date:
        raise ValueError('as_of must be an explicit datetime.date')
    if published_after is not None and (type(published_after) is not date or published_after > as_of):
        raise ValueError('published_after must be a date no later than as_of')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be an integer from 1 to 100')
    if type(include_undated) is not bool:
        raise ValueError('include_undated must be boolean')
    if max_per_document is not None and (type(max_per_document) is not int or not 1 <= max_per_document <= 100):
        raise ValueError('max_per_document must be an integer from 1 to 100')
    if source_files is not None:
        if not isinstance(source_files, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in source_files
        ):
            raise ValueError('source_files must be a list of filenames')
        if not source_files:
            return []
    prepared = natural_query(query) if query_mode == 'natural' else query.strip()
    if not prepared:
        return []
    return store.search_chunks(query=prepared, natural=query_mode == 'natural', as_of=as_of,
                               since=published_after, sources=source_files, undated=include_undated,
                               per_document=max_per_document, limit=limit)
