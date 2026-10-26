import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ingestion.common import write_json
from ingestion.database import validate_document
from ingestion.news_documents import prepare_news, artifact_paths
from ingestion.tagging import Vocabulary, Tagger, SemanticSector, choose_sector, tag_artifacts
from ingestion.taxonomy import SECTORS


def article(key='one', text='Банк России сохранил ключевую ставку. В Китае вырос спрос на нефть.'):
    return dict(news_id=key, title='Новость', url='https://example.test/' + key, text=text,
                published_at='2026-10-24T10:00:00+03:00', source='Источник',
                date_source='rss', retrieval_eligible=True, quality_flags=[])


class FixedSector:
    settings = dict(backend='test')
    def predict(self, texts):
        return [dict(value='finance', score=0.7, okved_range='64-66', candidates=[]) for _ in texts]


def vocabulary(root):
    path = root / 'companies.csv'
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['FTS_FLAG','FTS_COUNTRY_NAME','REGION'], delimiter=';')
        writer.writeheader()
        for region in ['Москва', 'Московская область', 'Башкортостан (Республика)', 'Республика Татарстан']:
            writer.writerow(dict(FTS_FLAG='1', FTS_COUNTRY_NAME='КИТАЙ, США, ТУРЦИЯ', REGION=region))
    return Vocabulary(path), path


class NewsTests(unittest.TestCase):
    def test_partial_content_is_explicit_and_preserves_source_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full = article()
            title = dict(article('title', ''), title='В Китае вырос спрос на нефть',
                         content_status='metadata_only', retrieval_eligible=False,
                         quality_flags=['title_only', 'article_not_fetched'])
            excerpt = dict(article('excerpt', 'Поставки выросли на 10%.'), title='Экспорт в Китай',
                           content_status='short_excerpt', retrieval_eligible=False,
                           quality_flags=['not_full_article', 'excerpt_truncated'])
            review = dict(article('review', ''), title='Будущая новость',
                          content_status='metadata_only', retrieval_eligible=False,
                          quality_flags=['title_only', 'future_publication_date'])
            blocked = dict(article('blocked'), content_status='partial_access', retrieval_eligible=False)
            duplicate = dict(title, news_id='duplicate', duplicate_of='title')
            source = root / 'combined.json'
            write_json(source, [full, title, excerpt, review, blocked, duplicate])
            strict = prepare_news(source, root / 'strict')
            self.assertEqual(strict['counts']['accepted'], 1)
            report = prepare_news(source, root / 'all', include_partial=True)
            self.assertEqual(report['counts']['accepted'], 4)
            self.assertEqual(report['counts']['needs_review'], 1)
            self.assertEqual(report['content_kinds'], dict(full_text=1, title_only=2, excerpt=1))
            self.assertEqual(report['rejection_reasons'], dict(source_not_eligible=1, source_duplicate=1))
            docs = {d['external_id']: d for p in artifact_paths(root / 'all')
                    for d in [json.loads(p.read_text(encoding='utf-8'))]}
            for doc in docs.values():
                validate_document(doc)
            self.assertEqual(docs['title']['pages'][0]['text'], title['title'])
            self.assertEqual(docs['excerpt']['pages'][0]['text'], 'Экспорт в Китай\n\nПоставки выросли на 10%.')
            self.assertFalse(docs['title']['pages'][0]['needs_review'])
            self.assertFalse(docs['excerpt']['pages'][0]['needs_review'])
            self.assertTrue(docs['review']['pages'][0]['needs_review'])
            self.assertFalse(docs['title']['source_metadata']['retrieval_eligible'])
            self.assertEqual(docs['title']['source_metadata']['quality_flags'], title['quality_flags'])
            self.assertTrue(docs['title']['source_metadata']['ingestion']['partial_retrieval_allowed'])

    def test_dry_run_does_not_write_or_invalidate_existing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'news.json'
            write_json(source, [article(), dict(article('title', ''), content_status='metadata_only',
                                              retrieval_eligible=False)])
            preview = prepare_news(source, root / 'missing', include_partial=True, dry_run=True)
            self.assertFalse((root / 'missing').exists())
            self.assertEqual(preview['counts']['accepted'], 2)
            actual = prepare_news(source, root / 'actual', include_partial=True)
            before = {p.name: p.read_bytes() for p in (root / 'actual').iterdir()}
            again = prepare_news(source, root / 'actual', include_partial=True, dry_run=True)
            self.assertEqual(again['counts'], actual['counts'])
            self.assertEqual(before, {p.name: p.read_bytes() for p in (root / 'actual').iterdir()})
            write_json(source, [])
            self.assertEqual(prepare_news(source, root / 'actual', dry_run=True)['counts']['rejected'], 0)
            self.assertEqual(before, {p.name: p.read_bytes() for p in (root / 'actual').iterdir()})

    def test_combined_filename_preserves_existing_document_and_chunk_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / 'old.json', [article()])
            write_json(root / 'combined.json', [article(), article('new', 'Новый материал.')])
            prepare_news(root / 'old.json', root / 'old')
            prepare_news(root / 'combined.json', root / 'combined', include_partial=True)
            old = json.loads(artifact_paths(root / 'old')[0].read_text(encoding='utf-8'))
            combined = json.loads((root / 'combined' / (old['document_id'] + '.news.json')).read_text(encoding='utf-8'))
            self.assertEqual(old['document_id'], combined['document_id'])
            self.assertEqual(old['pages'], combined['pages'])
            self.assertEqual(combined['source_metadata']['input_file'], 'combined.json')
            self.assertEqual(combined['source_file'], 'combined.json#one')

    def test_jsonl_quality_duplicates_offsets_and_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            items = [article(), article(), article('copy'), dict(article('no'), retrieval_eligible=False),
                     dict(article('bad'), published_at='not-a-date'), article('review', 'Текст требует проверки.')]
            items[-1]['quality_flags'] = ['short_body']
            source = root / 'news.jsonl'
            source.write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in items) + '\n{broken', encoding='utf-8')
            report = prepare_news(source, root / 'out')
            self.assertEqual(report['counts']['accepted'], 2)
            self.assertEqual(report['counts']['rejected'], 5)
            docs = [json.loads(p.read_text(encoding='utf-8')) for p in artifact_paths(root / 'out')]
            doc = next(d for d in docs if d['external_id'] == 'one')
            self.assertEqual(doc['published_at_timestamp'], '2026-10-24T10:00:00+03:00')
            self.assertEqual(doc['source_url'], 'https://example.test/one')
            validate_document(doc)
            self.assertTrue(next(d for d in docs if d['external_id'] == 'review')['pages'][0]['needs_review'])
            self.assertEqual(prepare_news(source, root / 'out'), report)

    def test_json_array_conflicts_and_stale_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'news.json'
            write_json(source, [article(), article('two', 'Новый отдельный текст.')])
            prepare_news(source, root / 'out')
            write_json(source, {'news': [article(), article('two', 'Различающийся текст.'), article('two', 'Другой текст.')]})
            result = prepare_news(source, root / 'out')
            self.assertEqual(result['counts']['accepted'], 1)
            self.assertEqual(result['rejection_reasons']['conflicting_news_id'], 2)
            self.assertEqual(len(artifact_paths(root / 'out')), 1)


class ImportRoutingTests(unittest.TestCase):
    def test_cli_combined_preview_routes_without_model_or_database(self):
        from ingestion.__main__ import main
        with patch('sys.argv', ['ingestion', 'prepare-news', '--source', 'combined.jsonl',
                                '--include-partial', '--dry-run']), \
                patch('ingestion.news_documents.prepare_news', return_value={}) as prepare, \
                patch('ingestion.tagging.SemanticSector', side_effect=AssertionError('No embeddings')), \
                patch('ingestion.database.connect', side_effect=AssertionError('No database')):
            main()
            prepare.assert_called_once_with('combined.jsonl', 'data/processed/news', 1400, 200,
                                            include_partial=True, dry_run=True)

    def test_explicit_formats_and_ambiguous_directory(self):
        from ingestion.__main__ import news_format
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.assertEqual(news_format(root,'snapshot'),'snapshot')
            with self.assertRaises(ValueError):news_format(root)
            (root/'manifest.json').write_text('{}')
            self.assertEqual(news_format(root),'chunks')
            (root/'report.json').write_text('{}');(root/'news.jsonl').write_text('')
            with self.assertRaises(ValueError):news_format(root)
            (root/'manifest.json').unlink()
            self.assertEqual(news_format(root),'snapshot')
            self.assertEqual(news_format(root/'news.jsonl'),'jsonl')


class TaggingTests(unittest.TestCase):
    def test_news_only_tags_title_and_excerpt_without_touching_pdf_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v, companies = vocabulary(root)
            rows = [dict(article('title', ''), title='Ключевая ставка в Китае',
                         content_status='metadata_only', retrieval_eligible=False),
                    dict(article('excerpt', 'Поставки выросли.'), title='Нефть из Китая',
                         content_status='short_excerpt', retrieval_eligible=False)]
            write_json(root / 'news.json', rows)
            prepare_news(root / 'news.json', root / 'prepared', include_partial=True)
            write_json(root / 'tagged/pdf/manifest.json', {'unchanged': True})
            pdf_manifest = (root / 'tagged/pdf/manifest.json').read_bytes()
            with patch('ingestion.tagging.SemanticSector', side_effect=AssertionError('No real embeddings')):
                report = tag_artifacts(None, root / 'prepared', root / 'tagged', companies,
                                       tagger=Tagger(v, FixedSector()), cache_dir=root / 'cache')
            self.assertNotIn('pdf_documents', report['counts'])
            self.assertEqual(report['counts']['news_documents'], 2)
            self.assertEqual((root / 'tagged/pdf/manifest.json').read_bytes(), pdf_manifest)
            for path in artifact_paths(root / 'tagged/news'):
                doc = json.loads(path.read_text(encoding='utf-8'))
                validate_document(doc)
                self.assertFalse(doc['pages'][0]['needs_review'])
                self.assertIn('country:КИТАЙ', doc['pages'][0]['chunks'][0]['tags'])
                self.assertEqual(doc['source_metadata']['ingestion']['partial_retrieval_allowed'], True)

    def test_chunk_factors_and_canonical_geography(self):
        with tempfile.TemporaryDirectory() as tmp:
            v, _ = vocabulary(Path(tmp))
            tagger = Tagger(v, FixedSector())
            first, second, third = tagger.classify([
                'ЦБ сохранил ключевую ставку. В Китае и США растет спрос на нефть. В Московской области строят жилье.',
                'Российские компании работают в Башкирии и Татарстане. Развитие рынка.',
                'Кредитный договор заключили в Москве. Поставка из Ирана.'])
            self.assertEqual(first['factors'], dict(key_rate=1, fx=1, commodities=1, consumer_demand=0, construction_market=1))
            self.assertIn('country:КИТАЙ', first['tags'])
            self.assertIn('country:США', first['tags'])
            self.assertIn('region:Московская область', first['tags'])
            self.assertNotIn('region:Москва', first['tags'])
            self.assertIn('region:Башкортостан (Республика)', second['tags'])
            self.assertIn('region:Республика Татарстан', second['tags'])
            self.assertEqual(second['factors']['fx'], 0)
            self.assertEqual(third['factors']['key_rate'], 0)  # Credit alone is not a key-rate mention.
            self.assertEqual(third['factors']['fx'], 1)
            self.assertIn('ИРАН', third['unmapped_countries'])
            self.assertNotIn('country:ИРАН', third['tags'])

    def test_sector_threshold_and_margin(self):
        scores = {k: 0.1 for k in SECTORS}
        self.assertIsNone(choose_sector(scores, 0.3, 0.03)['value'])
        scores['transport'] = 0.8
        self.assertEqual(choose_sector(scores, 0.3, 0.03)['value'], 'transport')
        scores['trade'] = 0.79
        self.assertIsNone(choose_sector(scores, 0.3, 0.03)['value'])

    def test_semantic_classifier_uses_tail_beyond_model_limit(self):
        import numpy as np
        class Tokenizer:
            def num_special_tokens_to_add(self, pair=False):
                return 2
            def encode(self, text, **kwargs):
                return text.split()
            def decode(self, tokens, **kwargs):
                return ' '.join(tokens)
        class Model:
            max_seq_length = 4
            tokenizer = Tokenizer()
            def encode(self, windows, **kwargs):
                self.windows = windows
                return np.array([[0., 1.] if 'transport' in w else [1., 0.] for w in windows])
        classifier = object.__new__(SemanticSector)
        classifier.np, classifier.model = np, Model()
        classifier.names = ['trade', 'transport']
        classifier.prototypes = np.eye(2)
        classifier.threshold, classifier.margin, classifier.batch_size = 0.3, 0.03, 32
        result = classifier.predict(['trade trade transport transport transport transport'])
        self.assertEqual(result[0]['value'], 'transport')
        self.assertEqual(len(classifier.model.windows), 3)

    def test_pdf_news_tagging_preserves_text_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v, companies = vocabulary(root)
            source = root / 'news.json'
            write_json(source, [article()])
            prepare_news(source, root / 'news')
            raw_doc = json.loads(artifact_paths(root / 'news')[0].read_text(encoding='utf-8'))
            pdf_doc = dict(raw_doc, source_type='pdf', source_file='test.pdf')
            write_json(root / 'pdf/test.pdf.json', pdf_doc)
            tagger = Tagger(v, FixedSector())
            result = tag_artifacts(root / 'pdf', root / 'news', root / 'tagged', companies,
                                   tagger=tagger, cache_dir=root / 'cache')
            doc = json.loads(artifact_paths(root / 'tagged/news')[0].read_text(encoding='utf-8'))
            validate_document(doc)
            self.assertEqual(doc['pages'][0]['text'], raw_doc['pages'][0]['text'])
            self.assertEqual(doc['pages'][0]['chunks'][0]['text'], raw_doc['pages'][0]['chunks'][0]['text'])
            self.assertEqual(result['counts']['chunks'], 2)
            tagger.classify = lambda _: self.fail('cached chunks must not be reclassified')
            again = tag_artifacts(root / 'pdf', root / 'news', root / 'tagged', companies,
                                  tagger=tagger, cache_dir=root / 'cache')
            self.assertEqual(result, again)


if __name__ == '__main__':
    unittest.main()
