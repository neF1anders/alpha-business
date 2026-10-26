"""SQLite cache of chunk labels keyed by classifier fingerprint and text hash."""
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3


class TagCache:
    def __init__(self, db):
        self.db = db

    def load(self, config, texts):
        return {h: json.loads(value) for h, value in
                self.db.execute('SELECT text_hash,result FROM tags WHERE config=?', (config,)) if h in texts}

    def save(self, config, text_hash, value):
        self.db.execute('INSERT OR REPLACE INTO tags VALUES (?,?,?)',
                        (config, text_hash, json.dumps(value, ensure_ascii=False)))

    def commit(self):
        self.db.commit()


class SqliteTagCache:
    @contextmanager
    def open(self, cache_dir):
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(Path(cache_dir) / 'chunk-tags.sqlite')) as db:
            db.execute('CREATE TABLE IF NOT EXISTS tags(config TEXT, text_hash TEXT, result TEXT, PRIMARY KEY(config,text_hash))')
            yield TagCache(db)
