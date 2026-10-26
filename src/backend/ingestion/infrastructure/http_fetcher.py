"""Bounded HTTP download restricted to the configured public news hosts."""
from urllib.request import Request, HTTPRedirectHandler, build_opener

from ..domain.clock import now
from ..domain.news_urls import canonical_url, digest

USER_AGENT = 'AlphaBusinessHackathon/0.1 (bounded research collector)'


class RestrictedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        canonical_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, limit=8_000_000):
    canonical_url(url)
    req = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
    with build_opener(RestrictedRedirect()).open(req, timeout=20) as response:
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('response exceeds byte limit')
        return raw, dict(url=response.url, status=response.status,
                         content_type=response.headers.get('Content-Type'), fetched_at=now(), sha256=digest(raw))


class UrllibFetcher:
    def download(self, url, limit=8_000_000):
        return download(url, limit)
