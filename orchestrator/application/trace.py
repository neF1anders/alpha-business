"""Трасса запроса и дедлайн. Трасса отвечает на вопрос «из каких шагов и признаков получился ответ»."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from ..domain.errors import DeadlineExceeded
from ..domain.models import TraceStep

log = logging.getLogger("orchestrator")


class Trace:
    def __init__(self, trace_id: str, on_step: Optional[Callable[[TraceStep], None]] = None) -> None:
        self.trace_id = trace_id
        self._on_step = on_step                      # например, отправка прогресса в WebSocket
        self.steps: list[TraceStep] = []
        self.degraded: list[str] = []

    @contextmanager
    def step(self, name: str, **detail) -> Iterator[dict]:
        """`with trace.step("retrieve") as d: d["found"] = 5`. Ошибка помечает шаг и пробрасывается дальше."""
        started = time.perf_counter()
        info = dict(detail)
        ok = True
        try:
            yield info
        except BaseException:
            ok = False
            raise
        finally:
            ms = (time.perf_counter() - started) * 1000
            step = TraceStep(name, round(ms, 1), ok, info)
            self.steps.append(step)
            if self._on_step:
                self._on_step(step)
            log.info("trace=%s step=%s ok=%s ms=%.1f %s", self.trace_id, name, ok, ms, info)

    def degrade(self, reason: str) -> None:
        self.degraded.append(reason)
        log.warning("trace=%s degraded: %s", self.trace_id, reason)


class Deadline:
    """Бюджет времени на запрос (SLO). `slice(0.4)` отдаёт долю от остатка для отдельного шага."""

    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + seconds

    def remaining(self) -> float:
        return max(self._end - time.monotonic(), 0.0)

    def slice(self, fraction: float) -> float:
        return self.remaining() * fraction

    def check(self) -> None:
        if self.remaining() <= 0:
            raise DeadlineExceeded()
