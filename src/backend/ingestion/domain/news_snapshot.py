"""Field contract of an audited news snapshot row and its timestamp rule."""
from datetime import datetime

CORE = ('news_id', 'url', 'title', 'text', 'source', 'source_kind',
        'published_at', 'event_at', 'fetched_at', 'text_sha256')


FIELDS = CORE + ('categories', 'source_record', 'raw_receipt', 'first_import_sha256')


# Headline/excerpt records without a fetched article (collector content_status).
BRIEF_FIELDS = ('news_id', 'url', 'title', 'excerpt', 'content_kind', 'source', 'source_kind',
                'published_at', 'fetched_at', 'categories', 'source_record', 'first_import_sha256')
BRIEF_STATUSES = frozenset({'short_excerpt', 'metadata_only'})


def brief_kind(title, excerpt):
    return 'excerpt' if excerpt and excerpt != title else 'title_only'


# Fetch metadata may vary between snapshots; original article content must not.
IMMUTABLE = ('url', 'title', 'text', 'source', 'source_kind', 'published_at', 'event_at', 'text_sha256')


def aware(value, name, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f'{name}: expected an ISO timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError(f'{name}: timezone required')
    return result
