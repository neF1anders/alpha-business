"""Opt-in PostgreSQL tests: create and drop a separate, random test database."""
import csv
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg import sql

from ingestion import database
from ingestion.common import write_json
from ingestion.csv_data import COMPANY_COLUMNS, LINK_COLUMNS, PAYMENT_COLUMNS, SOURCES, prepare_csv


@unittest.skipUnless(os.environ.get('RUN_DB_TESTS') == '1', 'PostgreSQL integration: set RUN_DB_TESTS=1')
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = psycopg.connect(os.environ.get('DATABASE_URL', ''), autocommit=True)
        cls.name = 'insights_test_' + uuid.uuid4().hex
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.connection_patch = patch('ingestion.database.connect',
                                     side_effect=lambda: psycopg.connect(cls.admin.info.dsn, dbname=cls.name))
        cls.connection_patch.start()
        database.initialize()
        database.initialize()  # Schema creation is idempotent.

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


if __name__ == '__main__':
    unittest.main()
