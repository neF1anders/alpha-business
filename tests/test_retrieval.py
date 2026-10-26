"""Retrieval regressions; DB tests use an isolated disposable database only."""
from datetime import date
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql

from tests.ingestion_support import database
from tests.ingestion_support import write_json
from retrieval import retrieve

AS_OF = date(2026, 10, 24)


class RetrievalValidationTests(unittest.TestCase):
    def test_reject_invalid_inputs_before_connecting(self):
        cases = [dict(query=''), dict(query='x' * 2001), dict(as_of='2026-10-24'),
                 dict(query_mode='unknown'), dict(limit=0), dict(limit=True), dict(include_undated='yes'),
                 dict(max_per_document=0), dict(source_files='test.pdf'),
                 dict(published_after=date(2026, 10, 25))]
        with patch('ingestion.infrastructure.postgres.connect') as connect:
            for case in cases:
                with self.subTest(case=case), self.assertRaises(ValueError):
                    retrieve(**(dict(query='ключевая ставка', as_of=AS_OF) | case))
            connect.assert_not_called()

    def test_explicit_empty_source_filter_returns_nothing(self):
        with patch('ingestion.infrastructure.postgres.connect') as connect:
            self.assertEqual(retrieve('ставка', as_of=AS_OF, source_files=[]), [])
            connect.assert_not_called()


@unittest.skipUnless(os.environ.get('RUN_DB_TESTS') == '1', 'PostgreSQL integration: set RUN_DB_TESTS=1')
class RetrievalDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = psycopg.connect(os.environ.get('DATABASE_URL', ''), autocommit=True)
        cls.addClassCleanup(cls.admin.close)
        cls.name = 'insights_test_' + uuid.uuid4().hex
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.addClassCleanup(lambda: cls.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name))))
        patcher = patch('ingestion.infrastructure.postgres.connect',
                        side_effect=lambda: psycopg.connect(cls.admin.info.dsn, dbname=cls.name))
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        database.initialize()
        with tempfile.TemporaryDirectory() as directory:
            for filename, published, review, count in [
                ('old.pdf', '2021-04-15', False, 1),
                ('latest.pdf', '2026-09-11', False, 2),
                ('future.pdf', '2026-10-25', False, 1),
                ('undated.pdf', None, False, 1),
                ('review.pdf', '2026-09-01', True, 1),
                ('legal.pdf', '2020-09-01', False, 1),
            ]:
                text = 'Компания Полюс. Ключевая ставка влияет на стоимость кредитования предприятий.'
                if filename == 'legal.pdf':
                    text = 'Контакты и дисклеймер\nКлючевая ставка и кредитование.'
                pages = [dict(page_number=i, text=text, method='tesseract', confidence=40 if review else 90,
                              needs_review=review, chunks=[dict(start_char=0, end_char=len(text), text=text)])
                         for i in range(1, count + 1)]
                artifact = dict(version=1, source_file=filename, title=filename, sha256='a' * 64,
                                published_at=published, date_source='synthetic fixture', settings={}, pages=pages)
                write_json(Path(directory) / (filename + '.pdf.json'), artifact)
            context = 'Компания Уникальная.\n\n' + 'Прочее описание. ' * 30
            continuation = 'Ограниченное раскрытие отчетности повышает риск.'
            text = context + continuation
            artifact = dict(version=1, source_file='context.pdf', title='Контекст', sha256='b' * 64,
                            published_at='2026-09-01', date_source='synthetic fixture', settings={},
                            pages=[dict(page_number=1, text=text, method='native', confidence=None, needs_review=False,
                                        chunks=[dict(start_char=0, end_char=len(context), text=context),
                                                dict(start_char=len(context), end_char=len(text), text=continuation)])])
            write_json(Path(directory) / 'context.pdf.json', artifact)
            database.load_pdfs(directory)

    def test_news_in_shared_chunks_never_becomes_bank_evidence(self):
        from tests.ingestion_support import prepare_news
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=root/'input.json'
            write_json(source,[dict(news_id='media-fixture',title='Ключевая ставка',
                text='Компания Полюс. Ключевая ставка влияет на стоимость кредитования предприятий.',
                url='https://example.test/media',source='СМИ',published_at='2026-10-23T10:00:00+00:00')])
            prepare_news(source,root/'chunks');database.load_news(root/'chunks')
            try:
                with database.connect() as c:
                    self.assertEqual(c.execute("SELECT count(*) FROM insights.rag_chunks WHERE source_type='news'").fetchone()[0],1)
                for mode in ('websearch','natural'):
                    found=retrieve('ключевая ставка',as_of=AS_OF,limit=100,query_mode=mode)
                    self.assertTrue(found)
                    self.assertTrue(all('media-fixture' not in r['source_file'] for r in found))
            finally:
                with database.connect() as c:c.execute("DELETE FROM insights.documents WHERE external_id='media-fixture'")

    def test_default_excludes_future_undated_and_bad_ocr(self):
        results = retrieve('ключевая ставка', as_of=AS_OF, limit=100)
        self.assertEqual(len(results), 4)
        self.assertEqual({r['source_file'] for r in results}, {'latest.pdf', 'old.pdf', 'legal.pdf'})

    def test_unknown_dates_require_explicit_opt_in(self):
        results = retrieve('ставка', as_of=AS_OF, include_undated=True, limit=100)
        self.assertIn('undated.pdf', {r['source_file'] for r in results})
        self.assertNotIn('future.pdf', {r['source_file'] for r in results})

    def test_date_window_and_source_filters(self):
        results = retrieve('ставка', as_of=AS_OF, published_after=date(2026, 1, 1), limit=100)
        self.assertEqual({r['source_file'] for r in results}, {'latest.pdf'})
        results = retrieve('ставка', as_of=AS_OF, source_files=['old.pdf'])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['source_file'], 'old.pdf')
        self.assertEqual(retrieve('ставка', as_of=date(2020, 1, 1)), [])

    def test_limit_document_cap_and_stable_order(self):
        self.assertEqual(len(retrieve('ставка', as_of=AS_OF, limit=1)), 1)
        results = retrieve('ставка', as_of=AS_OF, max_per_document=1)
        self.assertEqual(len(results), 3)
        self.assertEqual(len({r['document_id'] for r in results}), 3)
        self.assertEqual(results, retrieve('ставка', as_of=AS_OF, max_per_document=1))

    def test_morphology_and_source_offsets(self):
        results = retrieve('ключевой ставке', as_of=AS_OF)
        self.assertTrue(results)
        with database.connect() as conn:
            for result in results:
                text = conn.execute('SELECT text FROM insights.document_pages WHERE document_id=%s AND page_number=%s',
                                    (result['document_id'], result['page_number'])).fetchone()[0]
                self.assertEqual(result['text'], text[result['start_char']:result['end_char']])
                self.assertGreater(result['score'], 0)
                self.assertTrue(result['title'])

    def test_natural_question_finds_sources_while_strict_mode_misses(self):
        question = 'Почему ключевая ставка влияет на стоимость кредитования компании Полюс в перспективе?'
        self.assertEqual(retrieve(question, as_of=AS_OF), [])
        results = retrieve(question, as_of=AS_OF, query_mode='natural', limit=3)
        self.assertTrue(results)
        self.assertEqual({r['source_file'] for r in results}, {'latest.pdf', 'old.pdf'})
        self.assertTrue(all(r['matched_terms'] <= r['query_term_count'] for r in results))
        self.assertEqual(retrieve('Почему? Как?', as_of=AS_OF, query_mode='natural'), [])

    def test_natural_mode_respects_source_date_and_document_cap(self):
        results = retrieve('Почему ставка сохраняется?', as_of=AS_OF, query_mode='natural',
                           published_after=date(2026, 1, 1), max_per_document=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['source_file'], 'latest.pdf')
        self.assertEqual(retrieve('ставка', as_of=AS_OF, query_mode='natural', source_files=['future.pdf']), [])
        results = retrieve('ставка', as_of=AS_OF, query_mode='natural', include_undated=True,
                           source_files=['undated.pdf'])
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0]['published_at'])

    def test_natural_mode_uses_page_context_and_skips_disclaimer(self):
        results = retrieve('Почему Уникальная раскрывает отчетность?', as_of=AS_OF,
                           query_mode='natural', source_files=['context.pdf'])
        self.assertTrue(results)
        self.assertIn('Ограниченное раскрытие', results[0]['text'])
        self.assertNotIn('Уникальная', results[0]['text'])
        self.assertEqual(retrieve('ставка', as_of=AS_OF, query_mode='natural', source_files=['legal.pdf']), [])
        self.assertTrue(retrieve('ставка', as_of=AS_OF, source_files=['legal.pdf']))

    def test_query_is_parameterized_and_misses_return_empty(self):
        self.assertEqual(retrieve('несуществующаяабракадабра', as_of=AS_OF), [])
        retrieve("ставка'); DROP TABLE insights.documents; --", as_of=AS_OF)
        self.assertTrue(retrieve('ставка', as_of=AS_OF))


if __name__ == '__main__':
    unittest.main()
