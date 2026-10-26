"""Allowed news hosts, URL canonicalisation and content digests."""
import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

HOSTS = {'cbr.ru', 'www.cbr.ru', 'kommersant.ru', 'www.kommersant.ru'}


def digest(value):
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def canonical_url(url):
    parts = urlsplit(url.strip())
    if parts.scheme not in ('http', 'https') or parts.hostname not in HOSTS or parts.username or parts.password or parts.port not in (None, 80, 443):
        raise ValueError('URL is outside configured public sources')
    host = 'www.kommersant.ru' if 'kommersant' in parts.hostname else 'www.cbr.ru'
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('from', 'ref', 'fbclid', 'gclid')]
    return urlunsplit(('https', host, parts.path or '/', urlencode(sorted(query)), ''))
