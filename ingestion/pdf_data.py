"""Local OCR; page cache and source offsets make retrieval results traceable."""
import csv
import hashlib
import io
import json
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from .common import sha256, write_json


def chunks(text, size=1400, overlap=200):
    if size < 100 or not 0 <= overlap < size // 2:
        raise ValueError('chunk size must be >=100; overlap must be < size/2')
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind('\n\n', start + size // 2, end),
                           text.rfind('. ', start + size // 2, end))
            if boundary >= 0:
                end = boundary + 1
            else:
                boundary = text.rfind(' ', start + size // 2, end)
                if boundary >= 0:
                    end = boundary
        if text[start:end].strip():
            yield {'start_char': start, 'end_char': end, 'text': text[start:end]}
        if end == len(text):
            break
        next_start = max(start + 1, end - overlap)
        # Start the overlapping window at a word boundary.
        while next_start < end and next_start > 0 and not text[next_start - 1].isspace():
            next_start += 1
        start = next_start


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


def extract_pdfs(source_dir, output_dir, *, metadata_path=None, tesseract='tesseract',
                 languages='rus+eng', dpi=250, psm=3, timeout=180, size=1400,
                 overlap=200, min_confidence=60, force_ocr=False):
    import pymupdf

    if dpi < 72 or dpi > 600 or timeout <= 0 or not 0 <= min_confidence <= 100:
        raise ValueError('Invalid OCR options')
    list(chunks('test', size, overlap))  # Validate even when input is empty.
    source_dir, output_dir = Path(source_dir), Path(output_dir)
    files = sorted(source_dir.glob('*.pdf'))
    if not files:
        raise ValueError(f'No PDFs in {source_dir}')
    metadata = json.loads(Path(metadata_path).read_text(encoding='utf-8')) if metadata_path else {}
    settings = dict(version=1, languages=languages, dpi=dpi, psm=psm, size=size,
                    overlap=overlap, min_confidence=min_confidence, force_ocr=force_ocr,
                    pymupdf=pymupdf.VersionBind)
    # OCR version participates in the cache key; never reuse results from another engine silently.
    needs_ocr = force_ocr
    if not needs_ocr:
        for path in files:
            with pymupdf.open(path) as doc:
                if any(len(page.get_text().strip()) < 40 for page in doc):
                    needs_ocr = True
                    break
    settings['engine'] = check_tesseract(tesseract, languages) if needs_ocr else 'native'
    settings_id = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:16]
    report = []
    for path in files:
        digest = sha256(path)
        meta = metadata.get(path.name, {})
        published = meta.get('published_at')
        if published:
            date.fromisoformat(published)
        pages = []
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                raise ValueError(f'Encrypted PDF: {path.name}')
            for index, page in enumerate(doc, 1):
                cache = output_dir / 'pages' / digest / settings_id / f'{index}.json'
                if cache.exists():
                    value = json.loads(cache.read_text(encoding='utf-8'))
                else:
                    text = page.get_text(sort=True).strip().replace('\x00', '')
                    method, confidence = 'native', None
                    if force_ocr or len(text) < 40:
                        try:
                            text, confidence = ocr_page(page, tesseract, languages, dpi, psm, timeout)
                        except subprocess.TimeoutExpired as exc:
                            raise RuntimeError(f'{path.name}, page {index}: OCR exceeded {timeout}s') from exc
                        except subprocess.CalledProcessError as exc:
                            raise RuntimeError(f'{path.name}, page {index}: OCR failed: {exc.stderr}') from exc
                        method = 'tesseract'
                    review = not text.strip() or (method == 'tesseract' and
                                                 (confidence is None or confidence < min_confidence))
                    value = dict(page_number=index, text=text, method=method,
                                 confidence=confidence, needs_review=review,
                                 chunks=list(chunks(text, size, overlap)))
                    write_json(cache, value)
                pages.append(value)
                print(f'{path.name}: page {index}/{len(doc)} ({value["method"]})', flush=True)
        if sha256(path) != digest:
            raise RuntimeError(f'PDF changed during processing: {path}')
        artifact = dict(version=1, sha256=digest, source_file=path.name,
                        title=meta.get('title', path.stem), published_at=published,
                        date_source=meta.get('date_source'), settings=settings,
                        pages=pages)
        # One complete artifact per source filename; partial page caches cannot reach the DB.
        destination = output_dir / (path.name + '.json')
        write_json(destination, artifact)
        report.append(dict(file=path.name, pages=len(pages),
                           chunks=sum(len(p['chunks']) for p in pages),
                           review_pages=[p['page_number'] for p in pages if p['needs_review']]))
    write_json(output_dir / 'report.json', report)
    return report
