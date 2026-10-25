"""Профили компаний из data/processed/company_tags.csv (результат scripts/build_tags.py).

Файл читается один раз при первом обращении; дальше профиль собирается из словаря в памяти.
"""
from __future__ import annotations

import asyncio
import csv
import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

from ..domain.models import CompanyProfile
from ..domain.profile_rules import features_of, segment_of

log = logging.getLogger("orchestrator.profiles")


class CsvProfileRepository:
    def __init__(self, tags_path: str | Path) -> None:
        self._path = Path(tags_path)
        self._tags: Optional[dict[str, frozenset[str]]] = None
        self._lock = asyncio.Lock()

    async def warmup(self) -> None:
        """Загрузить теги заранее: холодное чтение занимает секунды и не должно съедать SLO первого запроса."""
        await self._load()

    async def get(self, company_id: str) -> Optional[CompanyProfile]:
        tags = await self._load()
        found = tags.get(company_id)
        if found is None:
            return None
        return CompanyProfile(company_id=company_id, segment_id=segment_of(found), tags=found,
                              features=features_of(found))

    async def _load(self) -> dict[str, frozenset[str]]:
        if self._tags is None:
            async with self._lock:
                if self._tags is None:
                    self._tags = await asyncio.to_thread(self._read)
        return self._tags

    def _read(self) -> dict[str, frozenset[str]]:
        if not self._path.exists():
            raise FileNotFoundError(f"нет файла тегов {self._path}: запустите scripts/build_tags.py")
        grouped: dict[str, set[str]] = defaultdict(set)
        with self._path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                grouped[row["synthetic_u"]].add(row["tag"])
        log.info("профили загружены: %d компаний из %s", len(grouped), self._path)
        return {k: frozenset(v) for k, v in grouped.items()}
