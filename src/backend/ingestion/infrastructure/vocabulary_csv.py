"""Company vocabulary (countries and regions) loaded from the prepared companies CSV."""
import csv
from pathlib import Path

from ..domain.tagging import Vocabulary
from .filesystem import sha256


def load_vocabulary(companies):
    import pymorphy3
    morph = pymorphy3.MorphAnalyzer()
    countries, regions = set(), set()
    fingerprint = sha256(companies)
    with Path(companies).open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream, delimiter=';'):
            # Vocabulary is all populated country names. FTS_FLAG can contradict them.
            countries.update(x.strip() for x in row['FTS_COUNTRY_NAME'].split(',') if x.strip())
            if row['REGION']:
                regions.add(row['REGION'])
    return Vocabulary(countries, regions, fingerprint, morph)
