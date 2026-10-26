"""Tag prepared PDF and news chunks with factors, geography and a semantic sector."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from ..domain.tagging import MODEL
from .prepare_news import artifact_paths


def tag_artifacts(storage, tag_cache, pdf_source, news_source, output, companies, *, model=MODEL, revision=None,
                  threshold=0.30, margin=0.03, cache_dir='data/cache', batch_size=32, tagger=None,
                  tagger_factory=None):
    if not 0 <= threshold <= 1 or not 0 <= margin <= 1 or batch_size < 1:
        raise ValueError('Invalid classifier threshold, margin or batch size')
    pdf_source = Path(pdf_source) if pdf_source is not None else None
    news_source, output = Path(news_source), Path(output)
    paths = [(p, 'pdf') for p in sorted(pdf_source.glob('*.pdf.json'))] if pdf_source else []
    if (news_source / 'manifest.json').exists():
        paths.extend((p, 'news') for p in artifact_paths(storage, news_source))
    if not paths:
        raise ValueError('No prepared PDF/news artifacts. Run extraction first.')
    if output.resolve() in {p.resolve() for p in (pdf_source, news_source) if p is not None}:
        raise ValueError('Tag output must be separate from the input directories')
    # tagger_factory loads the heavy models only after the cheap validation above.
    tagger = tagger or tagger_factory(companies, model, revision, threshold, margin,
                                      str(Path(cache_dir) / 'models'), batch_size)
    documents = [(p, kind, json.loads(p.read_text(encoding='utf-8'))) for p, kind in paths]
    texts = {hashlib.sha256(c['text'].encode()).hexdigest(): c['text']
             for _, _, doc in documents for page in doc['pages'] for c in page['chunks']}
    with tag_cache.open(cache_dir) as cache:
        results = cache.load(tagger.fingerprint, texts)
        missing = [h for h in texts if h not in results]
        for start in range(0, len(missing), batch_size):
            keys = missing[start:start + batch_size]
            labels = tagger.classify([texts[h] for h in keys])
            for h, value in zip(keys, labels):
                cache.save(tagger.fingerprint, h, value)
                results[h] = value
            cache.commit()
            if start % (batch_size * 10) == 0 or start + batch_size >= len(missing):
                print(f'Tagged {min(start + batch_size, len(missing))}/{len(missing)} new chunks ({len(texts) - len(missing)} cached)', flush=True)
    counts, distribution, unmapped = Counter(), Counter(), Counter()
    outputs = {'pdf': {}, 'news': {}}
    for path, kind, doc in documents:
        counts[kind + '_documents'] += 1
        doc['tagging'] = tagger.settings
        for page in doc['pages']:
            for chunk in page['chunks']:
                value = results[hashlib.sha256(chunk['text'].encode()).hexdigest()]
                chunk.update(value)
                counts['chunks'] += 1
                counts['without_sector'] += int(value['sector']['value'] is None)
                distribution.update(value['tags'])
                unmapped.update(value['unmapped_countries'])
        target = output / kind / path.name
        storage.write_json(target, doc)
        outputs[kind][path.name] = storage.sha256(target)
    # Both tagged directories have manifests: stale artifacts cannot silently be imported.
    for kind, files in outputs.items():
        if kind == 'pdf' and pdf_source is None:
            continue
        storage.write_json(output / kind / 'manifest.json', dict(version=1, outputs=files, tagging=tagger.settings))
    report = dict(counts=dict(counts), tags=dict(distribution.most_common()),
                  unmapped_countries=dict(unmapped), classifier=tagger.settings)
    storage.write_json(output / 'report.json', report)
    return report
