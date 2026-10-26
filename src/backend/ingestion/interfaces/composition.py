"""Default wiring of ingestion ports to local adapters (composition root helpers)."""
from ..domain.tagging import Tagger
from ..infrastructure.filesystem import LocalArtifactStorage
from ..infrastructure.sentence_sector import SemanticSector
from ..infrastructure.tagging_sources import tagging_source_digests
from ..infrastructure.vocabulary_csv import load_vocabulary


def create_storage():
    return LocalArtifactStorage()


def create_tagger(companies, model, revision, threshold, margin, cache_dir, batch_size):
    return Tagger(load_vocabulary(companies),
                  SemanticSector(model, revision, threshold, margin, cache_dir, batch_size),
                  tagging_source_digests())
