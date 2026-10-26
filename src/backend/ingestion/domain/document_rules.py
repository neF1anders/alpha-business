"""Validation of prepared document artifacts before they reach the database."""
from .taxonomy import FACTORS, SECTORS


def validate_document(doc):
    if doc.get('version') != 1 or not doc['pages']:
        raise ValueError('Invalid document artifact')
    if [p['page_number'] for p in doc['pages']] != list(range(1, len(doc['pages']) + 1)):
        raise ValueError('Missing or duplicate PDF pages')
    for page in doc['pages']:
        for chunk in page['chunks']:
            start, end = chunk['start_char'], chunk['end_char']
            if not 0 <= start < end <= len(page['text']) or page['text'][start:end] != chunk['text']:
                raise ValueError('Chunk does not match source page')
            if 'tagging_version' in chunk:
                tags = chunk.get('tags')
                factors = chunk.get('factors', {})
                if not isinstance(tags, list) or any(not isinstance(tag, str) or ':' not in tag for tag in tags):
                    raise ValueError('Invalid chunk tags')
                if set(factors) != set(FACTORS) or any(type(v) is not int or v not in (0, 1) for v in factors.values()):
                    raise ValueError('Invalid factor flags')
                if {tag for tag in tags if tag.startswith('factor:')} != {'factor:' + k for k,v in factors.items() if v}:
                    raise ValueError('Factor flags and tags disagree')
                for tag in tags:
                    namespace, value = tag.split(':', 1)
                    if namespace not in {'factor', 'sector', 'country', 'region'} or not value:
                        raise ValueError('Unknown tag namespace or empty tag')
                    if namespace == 'sector' and value not in SECTORS:
                        raise ValueError('Unknown sector')
