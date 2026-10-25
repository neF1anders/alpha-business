import unittest
import tempfile
import json
from pathlib import Path

from ingestion.news_data import canonical_url, extract_article, merge_urls, parse_feed, published_date, load_article_cache, digest


class NewsDataTests(unittest.TestCase):
    def test_cache_verifies_content_and_preserves_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = b'<p>Article</p>'
            (root / 'article.html').write_bytes(raw)
            receipt = dict(kind='article', news_id='n1', path='article.html',
                           sha256=digest(raw), fetched_at='2026-10-24T10:00:00+00:00')
            (root / 'receipts.jsonl').write_text(json.dumps(receipt) + '\n')
            cached, meta = load_article_cache(root)['n1']
            self.assertEqual(cached, raw)
            self.assertEqual(meta['fetched_at'], receipt['fetched_at'])
            (root / 'article.html').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                load_article_cache(root)

    def test_preserve_cbr_identity_and_remove_tracking(self):
        self.assertEqual(canonical_url('http://cbr.ru/press/PR/?utm_source=x&file=a.htm#x'),
                         'https://www.cbr.ru/press/PR/?file=a.htm')
        for url in ['https://evil.test/doc/1', 'file:///tmp/a', 'https://user:secret@cbr.ru/']:
            with self.assertRaises(ValueError):
                canonical_url(url)

    def test_utc_dates_and_unknown(self):
        self.assertEqual(published_date('Fri, 23 Oct 2026 13:30:00 +0300'), '2026-10-23T10:30:00+00:00')
        self.assertIsNone(published_date('unknown'))
        self.assertIsNone(published_date('Fri, 23 Oct 2026 13:30:00'))

    def test_scoped_html_extraction_preserves_words_and_ignores_noise(self):
        html = '<nav>Меню</nav><div class="landing-text"><p>Рост <b>цен</b> важен.</p><script>bad()</script><p>Ставка 14%.</p></div><footer>Подписка</footer>'
        text, restricted, _ = extract_article(html, 'cbr')
        self.assertEqual(text, 'Рост цен важен.\n\nСтавка 14%.')
        self.assertFalse(restricted)

    def test_multiple_kommersant_paragraphs_and_paywall(self):
        html = '<div class="doc-text-block__paragraph">Первый <a href="/">абзац</a>.</div><div>Реклама</div><div class="doc-text-block__paragraph">Второй.</div><div class="paywall">Купить</div>'
        text, restricted, count = extract_article(html, 'kommersant')
        self.assertEqual(text, 'Первый абзац.\n\nВторой.')
        self.assertTrue(restricted)
        self.assertEqual(count, 2)

    def test_feed_rejects_external_url_and_keeps_provenance(self):
        raw = b'<rss><channel><item><title>A</title><link>https://www.kommersant.ru/doc/1</link><description>&lt;p&gt;Text&lt;/p&gt;</description></item><item><title>B</title><link>https://evil.test/</link></item></channel></rss>'
        records, errors = parse_feed(raw, 'feed1', 'source', 'media', 'feed_url', 'now')
        self.assertEqual(len(records), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(records[0]['rss_description'], 'Text')
        self.assertIsNone(records[0]['published_at'])
        self.assertIsNone(records[0]['event_at'])
        second = dict(records[0], feed_ids=['feed2'], feed_urls=['feed2_url'])
        merged = merge_urls(records + [second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['feed_ids'], ['feed1', 'feed2'])


if __name__ == '__main__':
    unittest.main()
