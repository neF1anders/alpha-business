import unittest

from retrieval.adapter import bank_materials
from retrieval.query import natural_query


class AdapterTests(unittest.TestCase):
    def chunk(self, chunk_id='chunk1', page=2):
        return dict(chunk_id=chunk_id, document_id='document1', title='Банковский отчёт',
                    page_number=page, text='Подтверждающий исходный текст.')

    def test_two_fragments_of_one_pdf_keep_distinct_ids(self):
        result = bank_materials([self.chunk(), self.chunk('chunk2', 3)],
                                {'document1': 'https://bank.example/reports/one.pdf'})
        self.assertEqual([row['doc_id'] for row in result], ['bank:chunk1', 'bank:chunk2'])
        self.assertEqual(len({row['doc_id']: row for row in result}), 2)
        self.assertEqual(result[1]['url'], 'https://bank.example/reports/one.pdf#page=3')
        self.assertEqual(set(result[0]), {'doc_id', 'title', 'page', 'claim_text', 'url'})

    def test_deduplicate_same_fragment_and_keep_url_query(self):
        result = bank_materials([self.chunk(), self.chunk()],
                                {'document1': '/reports/one.pdf?download=1#old'})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['url'], '/reports/one.pdf?download=1#page=2')

    def test_no_invented_url_or_local_path(self):
        for urls in [{}, {'document1': 'file:///private/report.pdf'},
                     {'document1': '//other.example/report.pdf'},
                     {'document1': 'https://user:password@example.com/file.pdf'}]:
            with self.subTest(urls=urls), self.assertRaises(ValueError):
                bank_materials([self.chunk()], urls)
        for page in [0, True, '2']:
            with self.subTest(page=page), self.assertRaises(ValueError):
                bank_materials([self.chunk(page=page)], {'document1': '/one.pdf'})


class QueryTests(unittest.TestCase):
    def test_question_cleanup_preserves_business_terms(self):
        self.assertEqual(natural_query('Почему Полюс: отчётность, отчётность?'), 'полюс отчетность')
        self.assertIn('русгидро', natural_query('Как влияет ставка на РусГидро?'))
        self.assertEqual(natural_query('Почему? Как?'), '')
        self.assertIn('2026', natural_query('Какой прогноз на 2026 год?'))
