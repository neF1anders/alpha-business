"""Digests of the tagging rule files; any edit invalidates cached chunk labels."""
from pathlib import Path

from .filesystem import sha256

DOMAIN = Path(__file__).resolve().parents[1] / 'domain'


def tagging_source_digests():
    return dict(rules_sha256=sha256(DOMAIN / 'taxonomy.py'), code_sha256=sha256(DOMAIN / 'tagging.py'))
