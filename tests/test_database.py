"""Opt-in PostgreSQL tests: create and drop a separate, random test database."""
import csv
from datetime import datetime
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg import sql

from tests.ingestion_support import database
from tests.ingestion_support import write_json, sha256
from tests.ingestion_support import prepare_news, artifact_paths
from ingestion.domain.csv_rules import COMPANY_COLUMNS, LINK_COLUMNS, PAYMENT_COLUMNS, SOURCES
from tests.ingestion_support import prepare_csv


@unittest.skipUnless(os.environ.get('RUN_DB_TESTS') == '1', 'PostgreSQL integration: set RUN_DB_TESTS=1')
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = psycopg.connect(os.environ.get('DATABASE_URL', ''), dbname='postgres', autocommit=True)
        cls.name = 'insights_test_' + uuid.uuid4().hex
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        def test_connect():
            conn = psycopg.connect(cls.admin.info.dsn, dbname=cls.name)
            if conn.info.dbname != cls.name or not cls.name.startswith('insights_test_'):
                conn.close()
                raise AssertionError('Refusing to run tests outside the temporary database')
            return conn
        cls.connection_patch = patch('ingestion.infrastructure.postgres.connect', side_effect=test_connect)
        cls.connection_patch.start()
        # Start from the original schema, including its existing view and a populated PDF.
        legacy = database.SCHEMA.read_text(encoding='utf-8').split('-- Additive migration:')[0]
        with database.connect() as conn:
            conn.execute(legacy)
            conn.execute('''CREATE VIEW insights.rag_chunks AS
                SELECT c.chunk_id,c.document_id,d.title,d.source_file,d.published_at,d.date_source,
                c.page_number,c.text,c.start_char,c.end_char,p.method,p.confidence,c.search_vector
                FROM insights.document_chunks c JOIN insights.documents d USING(document_id)
                JOIN insights.document_pages p USING(document_id,page_number) WHERE NOT p.needs_review''')
            conn.execute("""INSERT INTO insights.documents(document_id,source_file,title,sha256,page_count,extraction_settings,artifact_sha256)
                VALUES ('legacy','legacy.pdf','Original document','original',1,'{}','original')""")
        database.initialize()
        database.initialize()  # Schema creation is idempotent.
        with database.connect() as conn:
            if conn.execute("SELECT title,source_type FROM insights.documents WHERE document_id='legacy'").fetchone() != ('Original document','pdf'):
                raise AssertionError('Migration lost the existing PDF')

    @classmethod
    def tearDownClass(cls):
        cls.connection_patch.stop()
        cls.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)))
        cls.admin.close()

    def test_snapshot_retrieval_repeat_load_and_document_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            company = {c: '' for c in COMPANY_COLUMNS}
            company.update(SYNTHETIC_U='U_1', MAINOKVED_CODE='01.10', NEGATIVEMARK='1', REVENUE_COMPANY='1000.0')
            fixtures = {
                'companies': [company],
                'person_links': [dict(zip(LINK_COLUMNS, ['F_1', 'Москва', 'Москва', 'U_1']))],
                'payments': [dict(zip(PAYMENT_COLUMNS, ['U_99', 'U_1', '123456789012345.12', '2']))]}
            for table, (filename, columns) in SOURCES.items():
                with (root / filename).open('w', encoding='utf-8', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=columns, delimiter=';')
                    writer.writeheader()
                    writer.writerows(fixtures[table])
            prepare_csv(root, root / 'clean')
            self.assertEqual(database.load_csv(root / 'clean')['status'], 'loaded')
            self.assertEqual(database.load_csv(root / 'clean')['status'], 'unchanged')
            with database.connect() as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM insights.company_entities').fetchone()[0], 2)
                self.assertEqual(str(conn.execute('SELECT pay_amt FROM insights.payments').fetchone()[0]), '123456789012345.12')
                self.assertTrue(conn.execute('SELECT negativemark FROM insights.companies').fetchone()[0])
            # Tampering with a prepared CSV must leave the committed snapshot intact.
            with (root / 'clean/payments.csv').open('a') as stream:
                stream.write('\n')
            with self.assertRaises(ValueError):
                database.load_csv(root / 'clean')
            text = 'Ключевая ставка влияет на инфляцию.'
            page = dict(page_number=1, text=text, method='tesseract', confidence=90,
                        needs_review=False, chunks=[dict(start_char=0, end_char=len(text), text=text)])
            artifact = dict(version=1, source_file='test.pdf', title='Тест', sha256='a' * 64,
                            published_at=None, settings={}, pages=[page])
            write_json(root / 'pdf/test.pdf.json', artifact)
            self.assertEqual(database.load_pdfs(root / 'pdf')[0]['status'], 'loaded')
            self.assertEqual(database.load_pdfs(root / 'pdf')[0]['status'], 'unchanged')
            self.assertEqual(database.search('ключевая ставка')[0]['page_number'], 1)
            # Force a DB failure after deleting the old pages, then verify rollback.
            artifact['pages'][0]['method'] = None
            write_json(root / 'pdf/test.pdf.json', artifact)
            with self.assertRaises(psycopg.errors.NotNullViolation):
                database.load_pdfs(root / 'pdf')
            self.assertEqual(len(database.search('ключевая ставка')), 1)
            artifact['pages'][0]['method'] = 'tesseract'
            artifact['pages'][0]['needs_review'] = True
            write_json(root / 'pdf/test.pdf.json', artifact)
            database.load_pdfs(root / 'pdf')
            self.assertEqual(database.search('ключевая ставка'), [])

    def test_news_metadata_tags_and_reimport(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = dict(news_id='test-news', url='https://example.test/news', title='Экспорт',
                       text='Китайский экспорт нефти увеличился.', source='Test Publisher',
                       published_at='2026-10-24T10:00:00+03:00')
            write_json(root / 'input.json', [raw])
            prepare_news(root / 'input.json', root / 'prepared')
            path = artifact_paths(root / 'prepared')[0]
            doc = json.loads(path.read_text(encoding='utf-8'))
            chunk = doc['pages'][0]['chunks'][0]
            chunk.update(tags=['country:КИТАЙ','factor:fx','factor:commodities','sector:mining'],
                         factors=dict(key_rate=0,fx=1,commodities=1,consumer_demand=0,construction_market=0),
                         tagging_version='test', sector=dict(value='mining',okved_range='05-09',score=0.8))
            write_json(path, doc)
            manifest = json.loads((path.parent / 'manifest.json').read_text(encoding='utf-8'))
            manifest['outputs'][path.name] = sha256(path)
            write_json(path.parent / 'manifest.json', manifest)
            self.assertEqual(database.load_news(path.parent)[0]['status'], 'loaded')
            self.assertEqual(database.load_news(path.parent)[0]['status'], 'unchanged')
            result = database.search('экспорт нефти', tags=['country:КИТАЙ','factor:fx'])
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['source_url'], raw['url'])
            self.assertEqual(database.search('экспорт нефти', tags=['country:США']), [])
            with database.connect() as conn:
                row = conn.execute('SELECT source_type,external_id,publisher,published_at_timestamp FROM insights.documents WHERE external_id=%s', ('test-news',)).fetchone()
                self.assertEqual(row[:3], ('news','test-news','Test Publisher'))
                self.assertEqual(row[3], datetime.fromisoformat(raw['published_at']))


if __name__ == '__main__':
    unittest.main()
