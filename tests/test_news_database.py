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
from ingestion.news_database import load_news, read_snapshot
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
class NewsDatabaseTests(unittest.TestCase):
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


if __name__=='__main__':unittest.main()
