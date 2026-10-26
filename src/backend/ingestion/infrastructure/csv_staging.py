"""Disk-backed SQLite staging used to deduplicate million-row CSV tables."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import tempfile


class SqliteStaging:
    @contextmanager
    def open(self):
        with tempfile.TemporaryDirectory(prefix='csv-stage-') as tmp, \
                closing(sqlite3.connect(Path(tmp) / 'stage.db')) as db:
            yield db
