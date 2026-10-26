import unittest
import tempfile
import json
import gzip
from ingestion.domain.news_urls import digest
from pathlib import Path
from ingestion.application.archive_crawl import seed_snapshot,counts
from ingestion.domain.news_html import ArchiveParser,article_metadata
from ingestion.infrastructure.crawl_state import connect_state
from tests.ingestion_support import STORAGE


class ArchiveTests(unittest.TestCase):
    def test_seed_is_idempotent_and_persists_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'output';root.mkdir();(root/'raw').mkdir()
            seed=Path(tmp)/'seed';seed.mkdir()
            row=dict(news_id='news:abc',url='https://www.kommersant.ru/doc/1',retrieval_eligible=True,text_sha256='abc')
            (seed/'news.jsonl').write_text(json.dumps(row)+'\n')
            (seed/'receipts.jsonl').write_text('')
            db=connect_state(root)
            seed_snapshot(STORAGE,db,root,seed);seed_snapshot(STORAGE,db,root,seed)
            self.assertEqual(counts(db)['eligible'],1)
            self.assertEqual(counts(db)['processed'],1)
            db.close()

    def test_compressed_seed_preserves_original_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp) / 'seed'; seed.mkdir()
            raw = b'<html>source body</html>'
            (seed / 'article.gz').write_bytes(gzip.compress(raw))
            row = dict(news_id='news:abc', url='https://www.kommersant.ru/doc/1',
                       retrieval_eligible=True, text_sha256='abc')
            receipt = dict(news_id='news:abc', kind='article', path='article.gz',
                           compression='gzip', sha256=digest(raw), fetched_at='2026-10-24')
            (seed / 'news.jsonl').write_text(json.dumps(row)+'\n')
            (seed / 'receipts.jsonl').write_text(json.dumps(receipt)+'\n')
            root = Path(tmp) / 'output'; root.mkdir(); (root / 'raw').mkdir()
            db = connect_state(root)
            seed_snapshot(STORAGE, db, root, seed)
            stored = json.loads(db.execute('SELECT receipt FROM articles').fetchone()[0])
            self.assertEqual(gzip.decompress((root / stored['path']).read_bytes()), raw)
            self.assertEqual(stored['fetched_at'], receipt['fetched_at'])
            db.close()
            (seed / 'article.gz').write_bytes(gzip.compress(b'changed'))
            other = Path(tmp) / 'other'; other.mkdir(); (other / 'raw').mkdir()
            db = connect_state(other)
            with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                seed_snapshot(STORAGE, db, other, seed)
            db.close()

    def test_archive_ignores_header_links_and_preserves_rubrics(self):
        p=ArchiveParser()
        p.feed('<a href="/doc/999">advert</a><article class="rubric_lenta__item" data-article-url="https://www.kommersant.ru/doc/42" data-article-title="A &amp; B" data-article-rubric-ids="3,4"></article>')
        self.assertEqual(len(p.items),1)
        self.assertEqual(p.items[0]['title'],'A & B')
        self.assertEqual(p.items[0]['rubric_ids'],['3','4'])

    def test_article_date_comes_from_head_not_related_article(self):
        h='<head><meta property="article:published_time" content="2026-09-12T13:00:00+03:00"><meta property="og:title" content="Title"><link rel="canonical" href="https://www.kommersant.ru/doc/42"></head><body><meta property="article:published_time" content="2020-01-01T00:00:00Z"></body>'
        m=article_metadata(h)
        self.assertEqual(m['published_at'],'2026-09-12T10:00:00+00:00')
        self.assertEqual(m['canonical'],'https://www.kommersant.ru/doc/42')

    def test_unknown_or_naive_dates_remain_unknown(self):
        for value in ['invalid','2026-09-12T13:00:00']:
            self.assertIsNone(article_metadata(f'<head><meta property="article:published_time" content="{value}"></head>')['published_at'])


if __name__=='__main__':unittest.main()
