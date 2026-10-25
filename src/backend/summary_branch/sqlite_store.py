"""Локальное хранилище баннеров в одном файле SQLite (стандартная библиотека), без внешней БД и контейнера.

Реализует тот же `BannerStore`, что и `InMemoryBannerStore`: баннеры, состояния «показан» и «скрыт» переживают
перезапуск процесса. Путь `":memory:"` даёт чистое ОЗУ. Одно соединение под блокировкой: ветка вызывается из пула потоков.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import Banner, BannerDTO, utcnow
from .scoring import pick_top_k

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS banners (
    company_id  TEXT NOT NULL,
    banner_id   TEXT NOT NULL,
    cluster_id  TEXT NOT NULL,
    expires_ts  REAL NOT NULL,
    banner_json TEXT NOT NULL,
    dto_json    TEXT NOT NULL,
    PRIMARY KEY (company_id, banner_id)
);
CREATE INDEX IF NOT EXISTS banners_cluster ON banners (cluster_id);
CREATE INDEX IF NOT EXISTS banners_expires ON banners (expires_ts);
CREATE TABLE IF NOT EXISTS seen (
    company_id TEXT NOT NULL,
    banner_id  TEXT NOT NULL,
    PRIMARY KEY (company_id, banner_id)
);
CREATE TABLE IF NOT EXISTS suppressed (
    company_id TEXT NOT NULL,
    banner_id  TEXT NOT NULL,
    reason     TEXT NOT NULL,
    PRIMARY KEY (company_id, banner_id)
);
"""


class SqliteBannerStore:
    """Личный индекс баннеров компаний в локальном файле. Состояния «показан» и «скрыт» отдельно от баннеров."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path) if str(path) == ":memory:" else str(Path(path).expanduser()),
                                     check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")   # для ":memory:" игнорируется
            self._conn.execute("PRAGMA synchronous=NORMAL")   # с WAL безопасно, а fsync на каждую запись не нужен
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def put(self, banner: Banner, dto: BannerDTO) -> bool:
        """Идемпотентно по (company_id, banner_id); «показан» и «скрыт» повторная запись не сбрасывает.

        True, если баннер новый и не скрыт пользователем (его можно пушить). Скрытый баннер сохраняется, но False.
        """
        with self._lock, self._conn:
            self._drop_expired(utcnow())
            is_new = self._conn.execute("SELECT 1 FROM banners WHERE company_id=? AND banner_id=?",
                                        (banner.company_id, banner.banner_id)).fetchone() is None
            self._conn.execute(
                "INSERT INTO banners (company_id, banner_id, cluster_id, expires_ts, banner_json, dto_json) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (company_id, banner_id) DO UPDATE SET "
                "cluster_id=excluded.cluster_id, expires_ts=excluded.expires_ts, "
                "banner_json=excluded.banner_json, dto_json=excluded.dto_json",
                (banner.company_id, banner.banner_id, banner.key.cluster_id, banner.expires_at.timestamp(),
                 banner.model_dump_json(), dto.model_dump_json()))
            return is_new and not self._is_suppressed(banner.company_id, banner.banner_id)

    def get(self, company_id: str, banner_id: str, now: Optional[datetime] = None) -> Optional[BannerDTO]:
        now = now or utcnow()
        with self._lock:
            row = self._conn.execute(
                "SELECT dto_json FROM banners WHERE company_id=? AND banner_id=? AND expires_ts>?",
                (company_id, banner_id, now.timestamp())).fetchone()
            if row is None or self._is_suppressed(company_id, banner_id):
                return None
            return BannerDTO.model_validate_json(row[0])

    def top_k(self, company_id: str, k: int, now: Optional[datetime] = None) -> list[BannerDTO]:
        now = now or utcnow()
        with self._lock:
            rows = self._conn.execute(
                "SELECT b.banner_json, b.dto_json FROM banners b WHERE b.company_id=? AND b.expires_ts>? "
                "AND NOT EXISTS (SELECT 1 FROM suppressed s WHERE s.company_id=b.company_id AND s.banner_id=b.banner_id)",
                (company_id, now.timestamp())).fetchall()
            seen = {r[0] for r in self._conn.execute("SELECT banner_id FROM seen WHERE company_id=?", (company_id,))}
        dtos = {}
        pool = []
        for banner_json, dto_json in rows:
            banner = Banner.model_validate_json(banner_json)
            pool.append(banner)
            dtos[banner.banner_id] = dto_json
        return [BannerDTO.model_validate_json(dtos[b.banner_id]) for b in pick_top_k(pool, k, now, seen)]

    def mark_seen(self, company_id: str, banner_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO seen (company_id, banner_id) "
                "SELECT company_id, banner_id FROM banners WHERE company_id=? AND banner_id=?",
                (company_id, banner_id))

    def suppress(self, company_id: str, banner_id: str, reason: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO suppressed (company_id, banner_id, reason) VALUES (?, ?, ?)",
                               (company_id, banner_id, reason))
        log.info("баннер скрыт: company=%s banner=%s reason=%s", company_id, banner_id, reason)

    def invalidate_cluster(self, cluster_id: str, reason: str) -> int:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM seen WHERE EXISTS (SELECT 1 FROM banners b WHERE b.cluster_id=? "
                "AND b.company_id=seen.company_id AND b.banner_id=seen.banner_id)", (cluster_id,))   # новое событие снова непросмотренное
            removed = self._conn.execute("DELETE FROM banners WHERE cluster_id=?", (cluster_id,)).rowcount
        log.info("кластер снят с выдачи: cluster=%s removed=%d reason=%s", cluster_id, removed, reason)
        return removed

    def _is_suppressed(self, company_id: str, banner_id: str) -> bool:
        return self._conn.execute("SELECT 1 FROM suppressed WHERE company_id=? AND banner_id=?",
                                  (company_id, banner_id)).fetchone() is not None

    def _drop_expired(self, now: datetime) -> None:
        ts = now.timestamp()
        self._conn.execute(
            "DELETE FROM seen WHERE EXISTS (SELECT 1 FROM banners b WHERE b.expires_ts<=? "
            "AND b.company_id=seen.company_id AND b.banner_id=seen.banner_id)", (ts,))
        self._conn.execute("DELETE FROM banners WHERE expires_ts<=?", (ts,))
