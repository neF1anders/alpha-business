"""Страж бюджета шлюза: читает GET /key/info и останавливает прогон до превышения потолка трат.

Ключ берётся из GatewaySettings (.env), в вывод и в исключения не попадает.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

Fetch = Callable[[str, dict[str, str]], tuple[int, bytes]]


class BudgetStop(Exception):
    """Прогон нужно остановить: потолок трат достигнут или остаток дня мал."""


@dataclass(frozen=True)
class KeySnapshot:
    spend: float
    max_budget: float | None

    @property
    def remaining(self) -> float | None:
        return None if self.max_budget is None else self.max_budget - self.spend


def urllib_fetch(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 (адрес из настроек)
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def parse_key_info(payload: dict) -> KeySnapshot:
    """LiteLLM отдаёт {"info": {"spend": ..., "max_budget": ...}}; плоский вид тоже допускаем."""
    info = payload.get("info", payload)
    if "spend" not in info:
        raise ValueError(f"в ответе /key/info нет поля spend: ключи {sorted(info)}")
    max_budget = info.get("max_budget")
    return KeySnapshot(spend=float(info["spend"]), max_budget=None if max_budget is None else float(max_budget))


class BudgetGuard:
    """spend0 фиксируется при создании; потрачено нами = spend сейчас минус spend0 (учёт общий для ключа)."""

    def __init__(self, base_url: str, api_key: str, ceiling: float = 100.0, min_remaining: float = 50.0,
                 fetch: Fetch = urllib_fetch) -> None:
        self._url = f"{base_url.rstrip('/').removesuffix('/v1')}/key/info"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._fetch = fetch
        self.ceiling = ceiling
        self.min_remaining = min_remaining
        self.start = self.snapshot()

    def snapshot(self) -> KeySnapshot:
        status, raw = self._fetch(self._url, self._headers)
        if status != 200:
            raise BudgetStop(f"/key/info вернул HTTP {status}")
        return parse_key_info(json.loads(raw))

    @property
    def spent(self) -> float:
        return self.snapshot().spend - self.start.spend

    def check(self) -> KeySnapshot:
        """Бросает BudgetStop, если пора остановиться."""
        now = self.snapshot()
        spent = now.spend - self.start.spend
        if spent >= self.ceiling:
            raise BudgetStop(f"потолок трат: потрачено {spent:.2f} из {self.ceiling:.2f}")
        if now.remaining is not None and now.remaining < self.min_remaining:
            raise BudgetStop(f"остаток дня мал: {now.remaining:.2f} < {self.min_remaining:.2f}")
        return now


def allowed_calls(cost_per_call: float, budget: float, hard_cap: int) -> int:
    """Сколько вызовов можно сделать в рамках бюджета; при неизвестной цене работает только жёсткий предел."""
    if cost_per_call <= 0:
        return hard_cap
    return max(0, min(hard_cap, int(budget / cost_per_call)))
