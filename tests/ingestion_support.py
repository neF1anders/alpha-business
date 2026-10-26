"""Test helpers that wire ingestion use cases to their default local adapters."""
from types import SimpleNamespace

from ingestion.application import extract_pdfs as extract_pdfs_module
from ingestion.application import load_documents as load_documents_module
from ingestion.application import load_news_snapshot as load_news_snapshot_module
from ingestion.application import prepare_csv as prepare_csv_module
from ingestion.application import prepare_news as prepare_news_module
from ingestion.application import tag_artifacts as tag_artifacts_module
from ingestion.infrastructure import postgres
from ingestion.infrastructure.csv_staging import SqliteStaging
from ingestion.infrastructure.filesystem import LocalArtifactStorage
from ingestion.infrastructure.pdf_reader import PyMuPdfReader
from ingestion.infrastructure.postgres import PostgresDocumentStore
from ingestion.infrastructure.tag_cache import SqliteTagCache
from ingestion.infrastructure.tesseract import TesseractOcr

STORAGE = LocalArtifactStorage()
write_json = STORAGE.write_json
sha256 = STORAGE.sha256


def prepare_csv(source_dir, output_dir):
    return prepare_csv_module.prepare_csv(STORAGE, SqliteStaging(), source_dir, output_dir)


def prepare_news(source, output, size=1400, overlap=200, **options):
    return prepare_news_module.prepare_news(STORAGE, source, output, size, overlap, **options)


def artifact_paths(directory):
    return prepare_news_module.artifact_paths(STORAGE, directory)


def extract_pdfs(source_dir, output_dir, *, ocr=None, **options):
    return extract_pdfs_module.extract_pdfs(STORAGE, PyMuPdfReader(), ocr or TesseractOcr(),
                                            source_dir, output_dir, **options)


def tag_artifacts(*args, **options):
    return tag_artifacts_module.tag_artifacts(STORAGE, SqliteTagCache(), *args, **options)


def load_news_snapshot(directory):
    return load_news_snapshot_module.load_news_snapshot(PostgresDocumentStore(), directory)


def load_collector_jsonl(path, **options):
    return load_news_snapshot_module.load_collector_jsonl(PostgresDocumentStore(), path, **options)


# Namespace mirroring the former ingestion.database module; connect() is looked up at call
# time so tests can patch ingestion.infrastructure.postgres.connect.
database = SimpleNamespace(
    SCHEMA=postgres.SCHEMA,
    connect=lambda: postgres.connect(),
    initialize=lambda: load_documents_module.initialize(PostgresDocumentStore()),
    load_csv=lambda directory: load_documents_module.load_csv(STORAGE, PostgresDocumentStore(), directory),
    load_pdfs=lambda directory: load_documents_module.load_pdfs(STORAGE, PostgresDocumentStore(), directory),
    load_news=lambda directory: load_documents_module.load_news(STORAGE, PostgresDocumentStore(), directory),
    search=lambda query, limit=5, tags=None: load_documents_module.search(PostgresDocumentStore(), query, limit, tags),
)
