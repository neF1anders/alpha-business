"""Tesseract OCR engine; PDF pages are rendered with PyMuPDF."""
import csv
import io
import re
import subprocess
import tempfile
from pathlib import Path


def parse_tsv(tsv):
    paragraphs, confidence = {}, []
    for row in csv.DictReader(io.StringIO(tsv), delimiter='\t', quoting=csv.QUOTE_NONE):
        if row['level'] != '5' or not (row.get('text') or '').strip():
            continue
        key = (row['block_num'], row['par_num'])
        line = paragraphs.setdefault(key, {}).setdefault(row['line_num'], [])
        line.append(row['text'].strip())
        score = float(row['conf'])
        if score >= 0:
            confidence.append(score)
    text = '\n\n'.join('\n'.join(' '.join(words) for words in lines.values())
                       for lines in paragraphs.values())
    # Join words broken across lines; preserve paragraph boundaries.
    text = re.sub(r'(?<=[а-яА-ЯёЁa-zA-Z])-\n(?=[а-яА-ЯёЁa-zA-Z])', '', text)
    return text.replace('\x00', ''), (sum(confidence) / len(confidence) if confidence else None)


def check_tesseract(command, languages):
    try:
        version = subprocess.run([command, '--version'], check=True, capture_output=True,
                                 encoding='utf-8', errors='replace', timeout=30).stdout.splitlines()[0]
        installed = subprocess.run([command, '--list-langs'], check=True, capture_output=True,
                                   encoding='utf-8', errors='replace', timeout=30).stdout.splitlines()
    except FileNotFoundError as exc:
        raise RuntimeError('Tesseract not found. Use the ingest Docker image or install Tesseract with rus+eng.') from exc
    missing = set(languages.split('+')) - {s.strip() for s in installed}
    if missing:
        raise RuntimeError(f'Missing Tesseract languages: {sorted(missing)}')
    return version


def ocr_page(page, command, languages, dpi, psm, timeout):
    with tempfile.TemporaryDirectory(prefix='page-ocr-') as tmp:
        image = Path(tmp) / 'page.png'
        page.get_pixmap(dpi=dpi, alpha=False).save(image)
        result = subprocess.run([command, str(image), 'stdout', '-l', languages,
                                 '--psm', str(psm), 'tsv'], check=True, capture_output=True,
                                encoding='utf-8', errors='replace', timeout=timeout)
        return parse_tsv(result.stdout)


class TesseractOcr:
    def check(self, command, languages):
        return check_tesseract(command, languages)

    def recognize(self, page, command, languages, dpi, psm, timeout):
        return ocr_page(page, command, languages, dpi, psm, timeout)
