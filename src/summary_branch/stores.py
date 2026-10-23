"""In-memory реализации хранилищ. Интерфейсы рассчитаны на замену Redis (кэш и sorted set) и Postgres."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable, Optional

from .models import Banner, BannerDTO, ContentKey, SegmentSummary, utcnow
from .scoring import pick_top_k

log = logging.getLogger(__name__)


class InMemorySegmentCache:
    """Общее содержимое сегмента. Состояния нет, только TTL."""

    def __init__(self, clock: Callable[[], datetime] = utcnow) -> None:
        self._clock = clock
        self._items: dict[ContentKey, tuple[SegmentSummary, datetime]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: ContentKey) -> Optional[SegmentSummary]:
        entry = self._items.get(key)
        if entry is not None and self._clock() >= entry[1]:
            del self._items[key]
            entry = None
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        return entry[0]

    def put(self, item: SegmentSummary, ttl_s: int) -> None:
        self._items[item.key] = (item, self._clock() + timedelta(seconds=ttl_s))


class InMemoryBannerStore:
    """Личный индекс баннеров компаний. Состояния «показан» и «скрыт» живут отдельно от самих баннеров."""

    def __init__(self) -> None:
        self._banners: dict[str, dict[str, Banner]] = {}
        self._dtos: dict[str, BannerDTO] = {}
        self._seen: dict[str, set[str]] = {}
        self._suppressed: dict[str, dict[str, str]] = {}

    def put(self, banner: Banner, dto: BannerDTO) -> None:
        """Идемпотентно по banner_id; состояния seen/suppressed повторная запись не сбрасывает."""
        self._banners.setdefault(banner.company_id, {})[banner.banner_id] = banner
        self._dtos[banner.banner_id] = dto

    def top_k(self, company_id: str, k: int, now: Optional[datetime] = None) -> list[BannerDTO]:
        now = now or utcnow()
        hidden = self._suppressed.get(company_id, {})
        pool = [b for b in self._banners.get(company_id, {}).values()
                if b.banner_id not in hidden and not b.is_expired(now)]
        chosen = pick_top_k(pool, k, now, self._seen.get(company_id, set()))
        return [self._dtos[b.banner_id] for b in chosen]

    def mark_seen(self, company_id: str, banner_id: str) -> None:
        if banner_id in self._banners.get(company_id, {}):
            self._seen.setdefault(company_id, set()).add(banner_id)

    def suppress(self, company_id: str, banner_id: str, reason: str) -> None:
        self._suppressed.setdefault(company_id, {})[banner_id] = reason
        log.info("баннер скрыт: company=%s banner=%s reason=%s", company_id, banner_id, reason)

    def invalidate_cluster(self, cluster_id: str, reason: str) -> int:
        removed = 0
        for company_banners in self._banners.values():
            stale = [bid for bid, b in company_banners.items() if b.key.cluster_id == cluster_id]
            for banner_id in stale:
                del company_banners[banner_id]
                self._dtos.pop(banner_id, None)
                removed += 1
        log.info("кластер снят с выдачи: cluster=%s removed=%d reason=%s", cluster_id, removed, reason)
        return removed
