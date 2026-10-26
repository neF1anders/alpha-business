"""RSS feed catalogue and parsing of feed items into news records."""
from datetime import timezone
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET

from .news_html import plain
from .news_urls import canonical_url, digest

FEEDS = (
    ('cbr_press', 'Банк России', 'primary', 'https://cbr.ru/rss/RssPress'),
    ('kommersant_economics', 'Коммерсантъ', 'media', 'https://www.kommersant.ru/RSS/section-economics.xml'),
    ('kommersant_business', 'Коммерсантъ', 'media', 'https://www.kommersant.ru/RSS/section-business.xml'),
)


EXTENDED_FEEDS = (
    ('cbr_events', 'Банк России', 'primary', 'https://cbr.ru/rss/eventrss'),
    ('kommersant_money', 'Коммерсантъ', 'media', 'https://www.kommersant.ru/RSS/money.xml'),
    ('kommersant_telecom', 'Коммерсантъ', 'media', 'https://www.kommersant.ru/RSS/section-telecom.xml'),
)


def published_date(value):
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def parse_feed(raw, feed_id, source_name, source_kind, feed_url, fetched_at):
    root = ET.fromstring(raw)
    records, errors = [], []
    for index, item in enumerate(root.findall('./channel/item')):
        try:
            url = canonical_url(item.findtext('link') or '')
            title = plain(item.findtext('title') or '')
            if not title:
                raise ValueError('empty title')
            published_raw = item.findtext('pubDate')
            records.append(dict(news_id='news:' + digest(url)[:24], url=url,
                title=title, source=source_name, source_kind=source_kind,
                feed_ids=[feed_id], feed_urls=[feed_url], categories=[x.text for x in item.findall('category') if x.text],
                published_at=published_date(published_raw), published_at_raw=published_raw,
                date_source='rss_pubDate', event_at=None, fetched_at=fetched_at,
                rss_description=plain(item.findtext('description') or ''), event_id=None))
        except ValueError as exc:
            errors.append(dict(stage='feed_item', feed_id=feed_id, item_index=index, error=str(exc)))
    return records, errors


def merge_urls(records):
    unique = {}
    for record in records:
        if record['url'] not in unique:
            unique[record['url']] = record
        else:
            previous = unique[record['url']]
            for field in ('feed_ids', 'feed_urls', 'categories'):
                previous[field] = sorted(set(previous[field] + record[field]))
    return list(unique.values())


RUBRICS = {'3': 'Экономика', '4': 'Бизнес', '40': 'Финансы',
           '138': 'Телекоммуникации'}
