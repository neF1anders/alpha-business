"""Pure HTML parsing rules: visible article text, archive listings and head metadata."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import re

from .news_urls import canonical_url

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


BLOCKS = {'p', 'div', 'section', 'li', 'h1', 'h2', 'h3', 'h4', 'tr', 'blockquote', 'br'}


class TextParser(HTMLParser):
    """Extract visible selected elements, preserving paragraph boundaries."""
    def __init__(self, source=None):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.stack = []
        self.parts = []
        self.access_restricted = False
        self.selected_blocks = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set((a.get('class') or '').split())
        hidden = tag in ('script', 'style', 'noscript', 'nav', 'footer', 'button') or 'hidden' in a or a.get('aria-hidden') == 'true'
        marker = ' '.join([a.get('class') or '', a.get('id') or '']).lower()
        if any(word in marker for word in ('paywall', 'subscription-wall', 'paid-content')):
            self.access_restricted = True
            hidden = True
        selected = self.source is None
        if self.source == 'cbr':
            selected = 'landing-text' in classes
        elif self.source == 'kommersant':
            selected = bool(classes & {'doc-text-block__paragraph', 'article__text'})
        parent_selected = self.stack[-1][1] if self.stack else False
        parent_hidden = self.stack[-1][2] if self.stack else False
        active, skip = selected or parent_selected, hidden or parent_hidden
        if selected and not parent_selected:
            self.selected_blocks += 1
        if active and not skip and tag in BLOCKS:
            self.parts.append('\n')
        if tag not in VOID:
            self.stack.append((tag, active, skip))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                if self.stack[i][1] and not self.stack[i][2] and tag in BLOCKS:
                    self.parts.append('\n')
                del self.stack[i:]
                break

    def handle_data(self, data):
        active = self.stack[-1][1] if self.stack else self.source is None
        hidden = self.stack[-1][2] if self.stack else False
        if active and not hidden:
            self.parts.append(data)

    def text(self):
        lines = [re.sub(r'\s+', ' ', line).strip() for line in ''.join(self.parts).splitlines()]
        return '\n\n'.join(line for line in lines if line)


def plain(html):
    parser = TextParser()
    parser.feed(html)
    return parser.text()


def extract_article(html, source):
    parser = TextParser(source)
    parser.feed(html)
    # JSON-LD access flags can signal a partial article even with visible paragraphs.
    restricted = parser.access_restricted or bool(re.search(r'"isAccessibleForFree"\s*:\s*(?:false|"false")', html, re.I))
    return parser.text(), restricted, parser.selected_blocks


class ArchiveParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag != 'article' or 'rubric_lenta__item' not in (a.get('class') or '').split():
            return
        url = a.get('data-article-url', '')
        if not re.fullmatch(r'https://www\.kommersant\.ru/doc/\d+', url):
            return
        self.items.append(dict(url=canonical_url(url), title=a.get('data-article-title', ''),
                               rubric_ids=(a.get('data-article-rubric-ids') or '').split(',')))


class HeadParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.canonical = None
        self.in_head = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'head':
            self.in_head = True
        if not self.in_head:
            return
        if tag == 'meta':
            key = a.get('property') or a.get('name')
            if key and key not in self.meta:
                self.meta[key] = a.get('content', '')
        if tag == 'link' and a.get('rel') == 'canonical':
            self.canonical = a.get('href')

    def handle_endtag(self, tag):
        if tag == 'head':
            self.in_head = False


def article_metadata(html):
    parser = HeadParser()
    parser.feed(html)
    raw_date = parser.meta.get('article:published_time')
    published = None
    if raw_date:
        try:
            value = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
            if value.tzinfo is not None:
                published = value.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return dict(title=plain(parser.meta.get('og:title', '')), published_at=published,
                published_at_raw=raw_date, canonical=parser.canonical)
