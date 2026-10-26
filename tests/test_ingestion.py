import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from ingestion.domain.csv_rules import COMPANY_COLUMNS, LINK_COLUMNS, PAYMENT_COLUMNS, SOURCES
from tests.ingestion_support import prepare_csv
from ingestion.domain.chunking import chunks
from ingestion.infrastructure.tesseract import parse_tsv, check_tesseract, ocr_page
from tests.ingestion_support import extract_pdfs
from ingestion.domain.document_rules import validate_document


class CsvTests(unittest.TestCase):
    def test_conflicts_duplicates_unknown_endpoints_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def company(uid, city='Москва'):
                row = {key: '' for key in COMPANY_COLUMNS}
                row.update(SYNTHETIC_U=uid, CITY=city, MAINOKVED_CODE='01.10', NEGATIVEMARK='1.0', REVENUE_COMPANY='1000.0')
                return row
            fixtures = {
                'companies': [company('U_1'), company('U_1'), company('U_2'), company('U_2', 'Омск')],
                'person_links': [dict(zip(LINK_COLUMNS, ['F_1', 'Москва', 'МОСКОВСКИЙ', 'U_1'])),
                                 dict(zip(LINK_COLUMNS, ['F_2', '', '', 'U_2'])),
                                 dict(zip(LINK_COLUMNS, ['F_3', '', '', 'U_99']))],
                'payments': [dict(zip(PAYMENT_COLUMNS, values)) for values in [
                    ['U_99', 'U_1', '10000', '2'], ['U_99', 'U_1', '10000', '2'],
                    ['U_1', 'U_1', '200', '1'], ['U_2', 'U_1', '200', '1'],
                    ['U_88', 'U_1', '100', '1'], ['U_88', 'U_1', '200', '1'],
                    ['U_77', 'U_1', 'NaN', '1'], ['U_77', 'U_1', '100', '1.5']]]}
            for name, (filename, fields) in SOURCES.items():
                with (root / filename).open('w', encoding='utf-8-sig', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=fields, delimiter=';')
                    writer.writeheader()
                    writer.writerows(fixtures[name])
            output = root / 'clean'
            report = prepare_csv(root, output)
            self.assertEqual(report['conflicting_company_ids'], ['U_2'])
            self.assertEqual([report['tables'][name]['accepted'] for name in SOURCES], [1, 2, 1])
            for value in report['tables'].values():
                self.assertEqual(value['input'], value['accepted'] + sum(value['rejected'].values()))
            with (output / 'companies.csv').open(encoding='utf-8') as stream:
                row = next(csv.DictReader(stream, delimiter=';'))
            self.assertEqual(row['MAINOKVED_CODE'], '01.10')
            self.assertEqual(row['NEGATIVEMARK'], '1')
            self.assertEqual(row['REVENUE_COMPANY'], '1000.0')
            self.assertEqual(prepare_csv(root, output), report)
            audit = [json.loads(line) for line in (output / 'rejected.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertTrue(any(r['reason'] == 'conflicting_payment_pair' for r in audit))
            self.assertTrue(all('row' in r and 'file' in r and 'data' in r for r in audit))


class PdfTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('tesseract'), 'Tesseract binary is not installed')
    def test_real_tesseract_on_raster_pdf(self):
        import pymupdf
        check_tesseract('tesseract', 'rus+eng')
        with pymupdf.open() as source, pymupdf.open() as raster:
            page = source.new_page()
            page.insert_text((50, 100), 'Interest rates and inflation.', fontsize=24)
            image = page.get_pixmap(dpi=200).tobytes('png')
            scanned = raster.new_page()
            scanned.insert_image(scanned.rect, stream=image)
            self.assertEqual(scanned.get_text(), '')
            text, confidence = ocr_page(scanned, 'tesseract', 'rus+eng', 200, 3, 120)
            self.assertIn('inflation', text.lower())
            self.assertGreater(confidence, 60)

    def test_chunks_cover_text_and_keep_offsets(self):
        text = ('Ставка и инфляция влияют на бизнес.\n\n' * 100) + 'Конец.'
        result = list(chunks(text, 200, 30))
        covered = set()
        for item in result:
            self.assertEqual(item['text'], text[item['start_char']:item['end_char']])
            self.assertLessEqual(len(item['text']), 200)
            covered.update(range(item['start_char'], item['end_char']))
        self.assertEqual(covered, set(range(len(text))))
        with self.assertRaises(ValueError):
            list(chunks(text, 200, 150))

    def test_tsv_reading_order_and_confidence(self):
        header = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
        tsv = header + '5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t90\tИнфляция\n' + \
            '5\t1\t2\t1\t1\t1\t20\t0\t10\t10\t70\tСтавка\n'
        text, confidence = parse_tsv(tsv)
        self.assertEqual(text, 'Инфляция\n\nСтавка')
        self.assertEqual(confidence, 80)

    def test_real_pdf_native_and_ocr_cache(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'raw'
            source.mkdir()
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((50, 50), 'Native source text about inflation and interest rates. ' * 2)
                doc.new_page()  # Image/blank page exercises the OCR branch.
                doc.save(source / 'test.pdf')
            engine = Mock()
            engine.check.return_value = 'test-engine'
            engine.recognize.return_value = ('Текст распознан с низкой уверенностью.', 40)
            extract_pdfs(source, root / 'out', ocr=engine)
            extract_pdfs(source, root / 'out', ocr=engine)
            self.assertEqual(engine.recognize.call_count, 1)
            artifact = json.loads((root / 'out/test.pdf.json').read_text(encoding='utf-8'))
            self.assertEqual(artifact['pages'][0]['method'], 'native')
            self.assertTrue(artifact['pages'][1]['needs_review'])
            self.assertIsNone(artifact['published_at'])
            validate_document(artifact)
            artifact['pages'][0]['chunks'][0]['text'] = 'corrupt'
            with self.assertRaises(ValueError):
                validate_document(artifact)


if __name__ == '__main__':
    unittest.main()
