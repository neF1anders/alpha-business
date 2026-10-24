"""In-memory реализации хранилищ для MVP: один процесс, без персистентности.

Интерфейсы рассчитаны на замену Redis (кэш и sorted set) и Postgres. Данные наружу отдаются копиями,
доступ защищён блокировкой: бэк может вызывать ветку из пула потоков.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Callable, Optional

from .models import Banner, BannerDTO, ContentKey, SegmentSummary, utcnow
from .scoring import pick_top_k

log = logging.getLogger(__name__)


class InMemorySegmentCache:
    """Общее содержимое сегмента. Состояния нет, только TTL."""

    def __init__(self, clock: Callable[[], datetime] = utcnow) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._items: dict[ContentKey, tuple[SegmentSummary, datetime]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: ContentKey) -> Optional[SegmentSummary]:
        with self._lock:
            entry = self._items.get(key)
            if entry is not None and self._clock() >= entry[1]:
                del self._items[key]
                entry = None
            if entry is None:
                self.misses += 1
                return None
            self.hits += 1
            return entry[0].model_copy(deep=True)

    def put(self, item: SegmentSummary, ttl_s: int) -> None:
        with self._lock:
            now = self._clock()
            self._items = {k: v for k, v in self._items.items() if now < v[1]}   # заодно чистим просроченное
            self._items[item.key] = (item.model_copy(deep=True), now + timedelta(seconds=ttl_s))


    def invalidate_cluster(self, cluster_id: str) -> int:
        with self._lock:
            stale = [key for key in self._items if key.cluster_id == cluster_id]
            for key in stale:
                del self._items[key]
        log.info("кэш сегментов: кластер сброшен cluster=%s removed=%d", cluster_id, len(stale))
        return len(stale)


class InMemoryBannerStore:
    """Личный индекс баннеров компаний. Состояния «показан» и «скрыт» живут отдельно от самих баннеров."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._banners: dict[str, dict[str, Banner]] = {}
        self._dtos: dict[tuple[str, str], BannerDTO] = {}
        self._seen: dict[str, set[str]] = {}
        self._suppressed: dict[str, dict[str, str]] = {}

    def put(self, banner: Banner, dto: BannerDTO) -> bool:
        """Идемпотентно по banner_id; состояния seen/suppressed повторная запись не сбрасывает.

        True, если баннер новый и не скрыт пользователем (его можно пушить). Скрытый баннер сохраняется, но False.
        """
        with self._lock:
            self._drop_expired(utcnow())
            company_banners = self._banners.setdefault(banner.company_id, {})
            is_new = banner.banner_id not in company_banners
            company_banners[banner.banner_id] = banner.model_copy(deep=True)
            self._dtos[(banner.company_id, banner.banner_id)] = dto.model_copy(deep=True)
            return is_new and banner.banner_id not in self._suppressed.get(banner.company_id, {})

    def get(self, company_id: str, banner_id: str, now: Optional[datetime] = None) -> Optional[BannerDTO]:
        now = now or utcnow()
        with self._lock:
            banner = self._banners.get(company_id, {}).get(banner_id)
            if banner is None or banner.is_expired(now) or banner_id in self._suppressed.get(company_id, {}):
                return None
            return self._dtos[(company_id, banner_id)].model_copy(deep=True)

    def top_k(self, company_id: str, k: int, now: Optional[datetime] = None) -> list[BannerDTO]:
        now = now or utcnow()
        with self._lock:
            hidden = self._suppressed.get(company_id, {})
            pool = [b for b in self._banners.get(company_id, {}).values()
                    if b.banner_id not in hidden and not b.is_expired(now)]
            chosen = pick_top_k(pool, k, now, set(self._seen.get(company_id, set())))
            return [self._dtos[(company_id, b.banner_id)].model_copy(deep=True) for b in chosen]

    def mark_seen(self, company_id: str, banner_id: str) -> None:
        with self._lock:
            if banner_id in self._banners.get(company_id, {}):
                self._seen.setdefault(company_id, set()).add(banner_id)

    def suppress(self, company_id: str, banner_id: str, reason: str) -> None:
        with self._lock:
            self._suppressed.setdefault(company_id, {})[banner_id] = reason
        log.info("баннер скрыт: company=%s banner=%s reason=%s", company_id, banner_id, reason)

    def invalidate_cluster(self, cluster_id: str, reason: str) -> int:
        removed = 0
        with self._lock:
            for company_id, company_banners in self._banners.items():
                stale = [bid for bid, b in company_banners.items() if b.key.cluster_id == cluster_id]
                for banner_id in stale:
                    del company_banners[banner_id]
                    self._dtos.pop((company_id, banner_id), None)
                    self._seen.get(company_id, set()).discard(banner_id)   # новое событие снова считается непросмотренным
                    removed += 1
        log.info("кластер снят с выдачи: cluster=%s removed=%d reason=%s", cluster_id, removed, reason)
        return removed

    def _drop_expired(self, now: datetime) -> None:
        for company_id, company_banners in self._banners.items():
            for banner_id in [bid for bid, b in company_banners.items() if b.is_expired(now)]:
                del company_banners[banner_id]
                self._dtos.pop((company_id, banner_id), None)
                self._seen.get(company_id, set()).discard(banner_id)
