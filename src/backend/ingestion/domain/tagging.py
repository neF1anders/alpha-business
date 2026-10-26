"""Chunk-local factor/geography rules and zero-shot sector selection."""
import hashlib
import json
import re
from collections import defaultdict
from functools import lru_cache

from .taxonomy import (COUNTRY_ALIASES, EXTRA_COUNTRIES, FACTORS, FACTOR_PATTERNS,
                       REGION_ALIASES, SECTORS)

MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'


TOKEN = re.compile(r'[^\W\d_]+', re.UNICODE)


class Vocabulary:
    def __init__(self, countries, regions, fingerprint, morph):
        """countries/regions: names from the company vocabulary; morph: pymorphy3-like analyser."""
        self.morph = morph
        self.countries, self.regions = set(countries), set(regions)
        self.fingerprint = fingerprint
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


class Tagger:
    def __init__(self, vocabulary, sector_model, source_digests):
        """source_digests: {rules_sha256, code_sha256} of the rule files, computed by the caller."""
        self.vocabulary, self.sector_model = vocabulary, sector_model
        # Include rule source hashes so edits invalidate cached labels.
        self.settings = dict(version=1, vocabulary_sha256=vocabulary.fingerprint,
                             rules_sha256=source_digests['rules_sha256'],
                             code_sha256=source_digests['code_sha256'], sector=sector_model.settings)
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
