"""JSON-compatible BankMaterialRef payloads for feat/orchestrator2banners."""
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit


def bank_materials(chunks, document_urls):
    """Use chunk IDs as evidence IDs; preserve document identity separately upstream.

    document_urls maps document_id to an actual configured PDF URL. This function
    neither invents API routes nor claims that local files are served by HTTP.
    Multiple fragments of the same PDF must have distinct evidence IDs because
    the summary branch indexes BankMaterialRef objects by doc_id.
    """
    if not isinstance(document_urls, Mapping):
        raise ValueError('document_urls must map document_id to a configured PDF URL')
    result, seen = [], set()
    for chunk in chunks:
        chunk_id = chunk['chunk_id']
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            raise ValueError('chunk_id must be a nonempty string')
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        page = chunk['page_number']
        if type(page) is not int or page < 1:
            raise ValueError('page_number must be a positive integer')
        url = document_urls.get(chunk['document_id'])
        if not isinstance(url, str) or not url.strip():
            raise ValueError('A configured PDF URL is required for every document')
        parsed = urlsplit(url)
        if not ((parsed.scheme in {'https', 'http'} and parsed.netloc)
                or (not parsed.scheme and not parsed.netloc and url.startswith('/') and not url.startswith('//'))):
            raise ValueError('PDF URL must be HTTP(S) or a site-relative path')
        if parsed.username or parsed.password:
            raise ValueError('Credentials must not be embedded in PDF URLs')
        if not isinstance(chunk['text'], str) or not chunk['text'].strip():
            raise ValueError('Evidence text must not be empty')
        result.append(dict(doc_id='bank:' + chunk_id, title=chunk['title'], page=page,
                           claim_text=chunk['text'],
                           url=urlunsplit(parsed._replace(fragment='page=' + str(page)))))
    return result
