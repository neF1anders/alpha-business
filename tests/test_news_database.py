"""Exercise news imports against a disposable PostgreSQL database, never the project data."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql

from ingestion import database
from ingestion.news_database import load_collector_jsonl, load_news, read_collector_jsonl, read_snapshot
from ingestion.news_data import digest


def article(number, text=None):
    url = f'https://www.kommersant.ru/doc/{number}'
    text = text or ('Изменение ключевой ставки влияет на стоимость кредитов для бизнеса. ' * 5 + str(number))
    return dict(news_id='news:'+digest(url)[:24], url=url, title=f'Ключевая ставка: материал {number}',
                text=text, source='Коммерсантъ', source_kind='media',
                published_at='2026-06-01T12:00:00+03:00', event_at=None,
                fetched_at='2026-10-24T12:00:00+03:00', text_sha256=digest(text),
                categories=['Экономика'], retrieval_eligible=True, quality_flags=[])


def snapshot(root, rows):
    root.mkdir(parents=True, exist_ok=True)
    raw=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows).encode()
    (root/'news.jsonl').write_bytes(raw)
    receipts=[]
    for row in rows:
        filename=row['news_id'].split(':')[1]+'.html'
        body=row['text'].encode();(root/filename).write_bytes(body)
        receipts.append(dict(news_id=row['news_id'],kind='article',path=filename,sha256=digest(body)))
    (root/'receipts.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in receipts))
    (root/'report.json').write_text(json.dumps(dict(news_sha256=digest(raw),eligible=len(rows),target_met=False)))
    (root/'audit.json').write_text(json.dumps(dict(news_sha256=digest(raw),verified=True,records=len(rows))))
    return root


def collector_file(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')
    return path


def headline(number):
    return dict(article(number), text='', text_sha256=None, retrieval_eligible=False,
                content_status='metadata_only', quality_flags=['title_only'])


class CollectorJsonlTests(unittest.TestCase):
    def test_only_full_articles_are_read_and_import_is_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=collector_file(Path(tmp)/'news_combined.jsonl',[article(1),headline(2),article(3)])
            _,_,rows,report,audit=read_collector_jsonl(path)
            self.assertEqual(len(rows),2)
            self.assertEqual((report['input'],report['eligible'],report['skipped_partial']),(3,2,1))
            self.assertFalse(audit['verified'])

    def test_broken_eligible_record_and_duplicates_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            short=dict(article(1),text='Коротко.',text_sha256=digest('Коротко.'))
            with self.assertRaisesRegex(ValueError,'checksum/length'):
                read_collector_jsonl(collector_file(Path(tmp)/'short.jsonl',[short]))
            with self.assertRaisesRegex(ValueError,'Duplicate news_id'):
                read_collector_jsonl(collector_file(Path(tmp)/'dup.jsonl',[article(1),article(1)]))
            with self.assertRaisesRegex(ValueError,'No retrieval-eligible'):
                read_collector_jsonl(collector_file(Path(tmp)/'empty.jsonl',[headline(1)]))


class SnapshotTests(unittest.TestCase):
    def test_tampered_file_and_naive_dates_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=snapshot(Path(tmp)/'one',[article(1)])
            with (root/'news.jsonl').open('a') as f:f.write('\n')
            with self.assertRaisesRegex(ValueError,'checksum/audit'):
                read_snapshot(root)
            row=article(1);row['published_at']='2026-06-01T12:00:00'
            root=snapshot(Path(tmp)/'two',[row])
            with self.assertRaisesRegex(ValueError,'timezone required'):
                read_snapshot(root)


@unittest.skipUnless(os.environ.get('RUN_DB_TESTS') == '1', 'Set RUN_DB_TESTS=1')
class DisposableDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin=psycopg.connect(os.environ.get('DATABASE_URL',''),autocommit=True)
        cls.name='insights_news_test_'+uuid.uuid4().hex
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.connection_patch=patch('ingestion.database.connect',
            side_effect=lambda:psycopg.connect(cls.admin.info.dsn,dbname=cls.name))
        cls.connection_patch.start()
        database.initialize();database.initialize()

    @classmethod
    def tearDownClass(cls):
        cls.connection_patch.stop()
        cls.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)))
        cls.admin.close()


class NewsDatabaseTests(DisposableDatabase):
    def test_repeat_expand_conflict_rollback_and_russian_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            first=article(1)
            p=snapshot(root/'first',[first])
            self.assertEqual(load_news(p)['added'],1)
            self.assertEqual(load_news(p)['status'],'unchanged')
            second=article(2)
            p=snapshot(root/'expanded',[first,second])
            self.assertEqual(load_news(p)['added'],1)
            with database.connect() as c:
                self.assertEqual(c.execute('SELECT count(*) FROM insights.news_articles').fetchone()[0],2)
                self.assertEqual(c.execute('SELECT count(*) FROM insights.news_import_items').fetchone()[0],3)
                self.assertEqual(c.execute("SELECT count(*) FROM insights.news_articles WHERE search_vector @@ websearch_to_tsquery('russian', 'ставки')").fetchone()[0],2)
                saved=c.execute('SELECT source_record FROM insights.news_articles WHERE news_id=%s',
                                (first['news_id'],)).fetchone()[0]
                self.assertEqual(saved,first)
            changed=dict(first,title='Changed title')
            p=snapshot(root/'conflict',[article(3),changed])
            with self.assertRaisesRegex(ValueError,'Existing article content differs'):
                load_news(p)
            # A different URL with the same text must not leave a partial import.
            p=snapshot(root/'duplicate',[article(4),article(5,text=first['text'])])
            with self.assertRaises(psycopg.errors.UniqueViolation):
                load_news(p)
            with database.connect() as c:
                self.assertEqual(c.execute('SELECT count(*) FROM insights.news_articles').fetchone()[0],2)
                self.assertEqual(c.execute('SELECT count(*) FROM insights.news_imports').fetchone()[0],2)


class CollectorJsonlDatabaseTests(DisposableDatabase):
    def test_collector_jsonl_repeat_and_combined_file_add_only_new_articles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            old=collector_file(root/'news.jsonl',[article(10),article(11)])
            self.assertEqual(load_collector_jsonl(old)['added'],2)
            self.assertEqual(load_collector_jsonl(old)['status'],'unchanged')
            combined=collector_file(root/'news_combined.jsonl',[headline(12),article(10),article(13)])
            result=load_collector_jsonl(combined)
            self.assertEqual((result['added'],result['snapshot_records']),(1,2))
            with database.connect() as c:
                receipt=c.execute('SELECT raw_receipt FROM insights.news_articles WHERE news_id=%s',
                                  (article(13)['news_id'],)).fetchone()[0]
                self.assertEqual(receipt,dict(kind='collector_jsonl',verified=False,source_file='news_combined.jsonl'))


if __name__=='__main__':unittest.main()
