"""SQLite state of the resumable archive crawl."""
import sqlite3


def connect_state(root):
    db = sqlite3.connect(root / 'state.sqlite3')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS articles (
            news_id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE,
            record TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            eligible INTEGER NOT NULL DEFAULT 0, text_sha TEXT, receipt TEXT,
            error TEXT);
        CREATE INDEX IF NOT EXISTS articles_hash ON articles(text_sha);
        CREATE TABLE IF NOT EXISTS archive_pages (
            url TEXT PRIMARY KEY, receipt TEXT NOT NULL, count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    return db
