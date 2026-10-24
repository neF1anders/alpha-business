"""Chunk-local factor/geography rules and zero-shot semantic sector matching."""
import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from .common import sha256, write_json
from .news_documents import artifact_paths
from .taxonomy import (COUNTRY_ALIASES, EXTRA_COUNTRIES, FACTORS, FACTOR_PATTERNS,
                       REGION_ALIASES, SECTORS)

MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
TOKEN = re.compile(r'[^\W\d_]+', re.UNICODE)


class Vocabulary:
    def __init__(self, companies):
        import pymorphy3
        self.morph = pymorphy3.MorphAnalyzer()
        self.countries, self.regions = set(), set()
        self.fingerprint = sha256(companies)
        with Path(companies).open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream, delimiter=';'):
                # Vocabulary is all populated country names. FTS_FLAG can contradict them.
                self.countries.update(x.strip() for x in row['FTS_COUNTRY_NAME'].split(',') if x.strip())
                if row['REGION']:
                    self.regions.add(row['REGION'])
        if not self.countries or not self.regions:
            raise ValueError('Company vocabulary must contain countries and regions')
        self.phrases = defaultdict(set)
        for country in sorted(self.countries | set(EXTRA_COUNTRIES)):
            for name in [country] + COUNTRY_ALIASES.get(country, []) + EXTRA_COUNTRIES.get(country, []):
                self.phrases[self.tokens(name)].add(('country', country))
        for region in sorted(self.regions):
            aliases = [region] + REGION_ALIASES.get(region, [])
            # Support both "Бурятия (Республика)" and "Республика Бурятия".
            if '(Республика)' in region:
                base = region.split(' (Республика)')[0]
                aliases.extend([base, 'Республика ' + base])
            for alias in aliases:
                self.phrases[self.tokens(alias)].add(('region', region))
        self.lengths = sorted({len(key) for key in self.phrases}, reverse=True)

    @lru_cache(maxsize=100000)
    def lemma(self, word):
        return self.morph.parse(word.lower().replace('ё', 'е'))[0].normal_form.replace('ё', 'е')

    def tokens(self, text):
        return tuple(self.lemma(word) for word in TOKEN.findall(text))

    def mentions(self, text):
        words = self.tokens(text)
        found = defaultdict(set)
        for length in self.lengths:
            for index in range(len(words) - length + 1):
                phrase = words[index:index + length]
                for namespace, name in self.phrases.get(phrase, ()):
                    found[namespace].add(name)
        return words, found


def choose_sector(scores, threshold, margin):
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best, score = ranked[0]
    accepted = score >= threshold and score - ranked[1][1] >= margin
    return dict(value=best if accepted else None, score=round(float(score), 6),
                okved_range=SECTORS[best][0] if accepted else None,
                candidates=[dict(sector=k, score=round(float(v), 6)) for k, v in ranked[:3]])


class SemanticSector:
    def __init__(self, model=MODEL, revision=None, threshold=0.30, margin=0.03,
                 cache_dir='data/cache/models', batch_size=32):
        import numpy as np
        import torch
        from sentence_transformers import SentenceTransformer
        torch.set_num_threads(min(4, torch.get_num_threads()))
        self.np = np
        self.model = SentenceTransformer(model, revision=revision, cache_folder=cache_dir, device='cpu', trust_remote_code=False)
        self.names = list(SECTORS)
        self.threshold, self.margin, self.batch_size = threshold, margin, batch_size
        self.prototypes = self.model.encode([v[1] for v in SECTORS.values()],
                                             normalize_embeddings=True, show_progress_bar=False)
        resolved_revision = getattr(self.model[0].auto_model.config, '_commit_hash', None)
        self.settings = dict(backend='sentence-transformers', model=model,
                             revision=resolved_revision or revision, threshold=threshold, margin=margin)

    def predict(self, texts):
        # MiniLM accepts 128 tokens: cover ALL of a chunk using token windows.
        tokenizer = self.model.tokenizer
        limit = self.model.max_seq_length - tokenizer.num_special_tokens_to_add(pair=False)
        windows, owners, weights = [], [], []
        for owner, text in enumerate(texts):
            tokens = tokenizer.encode(text, add_special_tokens=False, truncation=False)
            for start in range(0, max(1, len(tokens)), limit):
                part = tokens[start:start + limit]
                windows.append(tokenizer.decode(part, skip_special_tokens=True))
                owners.append(owner)
                weights.append(max(1, len(part)))
        vectors = self.model.encode(windows, batch_size=self.batch_size,
                                    normalize_embeddings=True, show_progress_bar=False)
        sums = self.np.zeros((len(texts), vectors.shape[1]), dtype='float32')
        for owner, weight, vector in zip(owners, weights, vectors):
            sums[owner] += weight * vector
        sums /= self.np.maximum(self.np.linalg.norm(sums, axis=1, keepdims=True), 1e-12)
        scores = sums @ self.prototypes.T
        return [choose_sector(dict(zip(self.names, row)), self.threshold, self.margin) for row in scores]


class Tagger:
    def __init__(self, vocabulary, sector_model):
        self.vocabulary, self.sector_model = vocabulary, sector_model
        # Include rule source hashes so edits invalidate cached labels.
        self.settings = dict(version=1, vocabulary_sha256=vocabulary.fingerprint,
                             rules_sha256=sha256(Path(__file__).with_name('taxonomy.py')),
                             code_sha256=sha256(__file__), sector=sector_model.settings)
        self.fingerprint = hashlib.sha256(json.dumps(self.settings, sort_keys=True).encode()).hexdigest()

    def classify(self, texts):
        sector_results = self.sector_model.predict(texts)
        results = []
        for text, sector in zip(texts, sector_results):
            words, found = self.vocabulary.mentions(text)
            normalized = ' '.join(words)
            flags = {factor: int(any(re.search(pattern, normalized) for pattern in patterns))
                     for factor, patterns in FACTOR_PATTERNS.items()}
            flags['fx'] = int(bool(found['country'] - {'РОССИЯ'}))
            countries = sorted(found['country'] & self.vocabulary.countries)
            regions = sorted(found['region'])
            tags = [f'factor:{name}' for name in FACTORS if flags[name]]
            tags += ['country:' + name for name in countries]
            tags += ['region:' + name for name in regions]
            if sector['value']:
                tags.append('sector:' + sector['value'])
            results.append(dict(tags=sorted(tags), factors={k: flags[k] for k in FACTORS},
                                sector=sector, countries=countries, regions=regions,
                                country_mentions=sorted(found['country']),
                                unmapped_countries=sorted(found['country'] - self.vocabulary.countries),
                                tagging_version=self.fingerprint))
        return results


def tag_artifacts(pdf_source, news_source, output, companies, *, model=MODEL, revision=None,
                  threshold=0.30, margin=0.03, cache_dir='data/cache', batch_size=32, tagger=None):
    if not 0 <= threshold <= 1 or not 0 <= margin <= 1 or batch_size < 1:
        raise ValueError('Invalid classifier threshold, margin or batch size')
    pdf_source, news_source, output = Path(pdf_source), Path(news_source), Path(output)
    paths = [(p, 'pdf') for p in sorted(pdf_source.glob('*.pdf.json'))]
    if (news_source / 'manifest.json').exists():
        paths.extend((p, 'news') for p in artifact_paths(news_source))
    if not paths:
        raise ValueError('No prepared PDF/news artifacts. Run extraction first.')
    if output.resolve() in {pdf_source.resolve(), news_source.resolve()}:
        raise ValueError('Tag output must be separate from the input directories')
    tagger = tagger or Tagger(Vocabulary(companies), SemanticSector(model, revision, threshold, margin,
                                                                  str(Path(cache_dir) / 'models'), batch_size))
    documents = [(p, kind, json.loads(p.read_text(encoding='utf-8'))) for p, kind in paths]
    texts = {hashlib.sha256(c['text'].encode()).hexdigest(): c['text']
             for _, _, doc in documents for page in doc['pages'] for c in page['chunks']}
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(Path(cache_dir) / 'chunk-tags.sqlite')) as db:
        db.execute('CREATE TABLE IF NOT EXISTS tags(config TEXT, text_hash TEXT, result TEXT, PRIMARY KEY(config,text_hash))')
        results = {h: json.loads(value) for h, value in db.execute('SELECT text_hash,result FROM tags WHERE config=?', (tagger.fingerprint,)) if h in texts}
        missing = [h for h in texts if h not in results]
        for start in range(0, len(missing), batch_size):
            keys = missing[start:start + batch_size]
            labels = tagger.classify([texts[h] for h in keys])
            for h, value in zip(keys, labels):
                db.execute('INSERT OR REPLACE INTO tags VALUES (?,?,?)', (tagger.fingerprint, h, json.dumps(value, ensure_ascii=False)))
                results[h] = value
            db.commit()
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
        write_json(target, doc)
        outputs[kind][path.name] = sha256(target)
    # Both tagged directories have manifests: stale artifacts cannot silently be imported.
    for kind, files in outputs.items():
        write_json(output / kind / 'manifest.json', dict(version=1, outputs=files, tagging=tagger.settings))
    report = dict(counts=dict(counts), tags=dict(distribution.most_common()),
                  unmapped_countries=dict(unmapped), classifier=tagger.settings)
    write_json(output / 'report.json', report)
    return report
