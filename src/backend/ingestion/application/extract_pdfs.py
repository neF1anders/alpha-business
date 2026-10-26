"""PDF text extraction with OCR fallback; page cache and source offsets make results traceable."""
import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path

from ..domain.chunking import chunks


def extract_pdfs(storage, pdf_reader, ocr, source_dir, output_dir, *, metadata_path=None, tesseract='tesseract',
                 languages='rus+eng', dpi=250, psm=3, timeout=180, size=1400,
                 overlap=200, min_confidence=60, force_ocr=False):
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
                    pymupdf=pdf_reader.version)
    # OCR version participates in the cache key; never reuse results from another engine silently.
    needs_ocr = force_ocr
    if not needs_ocr:
        for path in files:
            with pdf_reader.open(path) as doc:
                if any(len(page.get_text().strip()) < 40 for page in doc):
                    needs_ocr = True
                    break
    settings['engine'] = ocr.check(tesseract, languages) if needs_ocr else 'native'
    settings_id = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:16]
    report = []
    for path in files:
        digest = storage.sha256(path)
        meta = metadata.get(path.name, {})
        published = meta.get('published_at')
        if published:
            date.fromisoformat(published)
        pages = []
        with pdf_reader.open(path) as doc:
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
                            text, confidence = ocr.recognize(page, tesseract, languages, dpi, psm, timeout)
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
                    storage.write_json(cache, value)
                pages.append(value)
                print(f'{path.name}: page {index}/{len(doc)} ({value["method"]})', flush=True)
        if storage.sha256(path) != digest:
            raise RuntimeError(f'PDF changed during processing: {path}')
        artifact = dict(version=1, sha256=digest, source_file=path.name,
                        title=meta.get('title', path.stem), published_at=published,
                        date_source=meta.get('date_source'), settings=settings,
                        pages=pages)
        # One complete artifact per source filename; partial page caches cannot reach the DB.
        destination = output_dir / (path.name + '.json')
        storage.write_json(destination, artifact)
        report.append(dict(file=path.name, pages=len(pages),
                           chunks=sum(len(p['chunks']) for p in pages),
                           review_pages=[p['page_number'] for p in pages if p['needs_review']]))
    storage.write_json(output_dir / 'report.json', report)
    return report
