"""In-process доставка событий открытым сессиям клиентов (реализация SignalSink и SignalStream).

Ветка саммари публикует из рабочих потоков, подписчики читают в цикле событий: передача через
`call_soon_threadsafe`. Один процесс; для нескольких реплик порт меняется на внешнюю очередь (см. docs/architecture.md).
"""
from __future__ import annotations

import asyncio
import logging
import threading

from ..domain.models import Banner, SignalEvent, raw_banner_id

log = logging.getLogger(__name__)

QUEUE_SIZE = 100


class SignalHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[SignalEvent]]]] = {}

    def publish_banners(self, company_id: str, banners: list[Banner]) -> None:
        """Сначала снимается «только новость» тех же кластеров, затем приходят готовые баннеры."""
        replaced = [raw_banner_id(b.cluster_id) for b in banners if b.cluster_id and not b.raw_news]
        if replaced:
            self.publish_invalidate(company_id, replaced)
        self._emit(company_id, SignalEvent("banners", banners=tuple(banners)))

    def publish_invalidate(self, company_id: str, banner_ids: list[str]) -> None:
        self._emit(company_id, SignalEvent("invalidate", banner_ids=tuple(banner_ids)))

    def subscribe(self, company_id: str) -> "Subscription":
        subscription = Subscription(self, company_id, asyncio.get_running_loop())
        with self._lock:
            self._subscribers.setdefault(company_id, set()).add(subscription.entry)
        return subscription

    def _unsubscribe(self, company_id: str, entry: tuple) -> None:
        with self._lock:
            self._subscribers.get(company_id, set()).discard(entry)

    def subscribers(self, company_id: str) -> int:
        with self._lock:
            return len(self._subscribers.get(company_id, ()))

    def _emit(self, company_id: str, event: SignalEvent) -> None:
        with self._lock:
            targets = list(self._subscribers.get(company_id, ()))
        for loop, queue in targets:
            try:
                loop.call_soon_threadsafe(_offer, queue, event)
            except RuntimeError:            # цикл подписчика уже закрыт: подписка исчезнет при выходе из генератора
                log.debug("signal hub: цикл подписчика закрыт company=%s", company_id)


def _offer(queue: asyncio.Queue[SignalEvent], event: SignalEvent) -> None:
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:               # медленный клиент перечитает GET /signals после переподключения
        log.warning("signal hub: очередь подписчика переполнена, событие %s отброшено", event.kind)


class Subscription:
    def __init__(self, hub: SignalHub, company_id: str, loop: asyncio.AbstractEventLoop) -> None:
        self._hub, self._company_id = hub, company_id
        self.entry = (loop, asyncio.Queue[SignalEvent](maxsize=QUEUE_SIZE))

    async def next(self) -> SignalEvent:
        return await self.entry[1].get()

    def close(self) -> None:
        self._hub._unsubscribe(self._company_id, self.entry)
