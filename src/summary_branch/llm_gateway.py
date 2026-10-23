"""Адаптер LLMClient к OpenAI-совместимому шлюзу команды. Только стандартная библиотека, без SDK."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator

log = logging.getLogger(__name__)

ALLOWED_MODELS = frozenset({
    "google/gemma-4-26b-a4b-it", "z-ai/glm-5.3-flash", "qwen/qwen3.6-35b-a3b",
    "deepseek/deepseek-v4.1-flash", "qwen/qwen3-vl-30b-a3b-instruct", "moonshotai/kimi-k2.6",
})
PRIMARY_BASE_URL = "https://changellenge.up.railway.app/v1"
FALLBACK_BASE_URL = "https://api.alfahack-gateway26.ru/v1"

_MSK = timezone(timedelta(hours=3))
_BUDGET_RESET_HOUR_MSK = 3
_BUDGET_MARKER = "budget has been exceeded"
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


class LLMGatewayError(Exception):
    """Шлюз недоступен или отклонил запрос. Для ветки это сбой клиента: кластер пропускается."""


class LLMBudgetExceeded(LLMGatewayError):
    """Суточный бюджет ключа исчерпан (сброс в 3:00 МСК)."""


class GatewaySettings(BaseModel):
    api_key: SecretStr
    base_urls: list[str] = Field(default_factory=lambda: [PRIMARY_BASE_URL, FALLBACK_BASE_URL], min_length=1)
    # Стартовое соответствие уровней моделям; подбирается по реальным прогонам.
    model_by_tier: dict[str, str] = Field(default_factory=lambda: {
        "full": "deepseek/deepseek-v4.1-flash", "lite": "z-ai/glm-5.3-flash"})
    timeout_s: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=1, ge=0)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=2000, gt=0)
    # Модели-«рассуждатели» иначе тратят весь max_tokens на скрытое reasoning и отдают пустой content (проверено на шлюзе).
    disable_reasoning: bool = True

    @field_validator("model_by_tier")
    @classmethod
    def _models_are_known(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value.values()) - ALLOWED_MODELS)
        if unknown:
            raise ValueError(f"модели нет в списке шлюза: {unknown}")
        return value

    @classmethod
    def from_env(cls, env_file: str | Path = ".env", environ: Optional[Mapping[str, str]] = None) -> "GatewaySettings":
        """Читает `.env` и переменные окружения (окружение приоритетнее). Ключ: API_KEY.

        Необязательные: LLM_BASE_URL (основной адрес), LLM_MODEL_FULL, LLM_MODEL_LITE.
        """
        values = {**_read_env_file(Path(env_file)), **(os.environ if environ is None else environ)}
        if not values.get("API_KEY"):
            raise LLMGatewayError("не найден API_KEY ни в окружении, ни в .env")
        settings: dict = {"api_key": values["API_KEY"]}
        if values.get("LLM_BASE_URL"):
            settings["base_urls"] = [values["LLM_BASE_URL"], FALLBACK_BASE_URL]
        models = {tier: values[name] for tier, name in (("full", "LLM_MODEL_FULL"), ("lite", "LLM_MODEL_LITE"))
                  if values.get(name)}
        if models:
            settings["model_by_tier"] = {**cls.model_fields["model_by_tier"].default_factory(), **models}
        return cls(**settings)


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        result[name.strip()] = value.strip().strip("'\"")
    return result


def urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (адрес из настроек)
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def parse_json_object(content: str) -> dict:
    """Достаёт JSON-объект из ответа модели: убирает <think>, markdown-ограждение и текст вокруг."""
    text = _FENCE.sub("", _THINK_BLOCK.sub("", content).strip()).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("ответ модели не является JSON-объектом")
    return parsed


class OpenAICompatibleLLMClient:
    """Реализация LLMClient: tier -> модель, запасной адрес, повторы, остановка при исчерпании суточного бюджета."""

    def __init__(self, settings: GatewaySettings, transport: Transport = urllib_transport,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._settings = settings
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._blocked_until: Optional[datetime] = None
        self._use_response_format = True
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost = 0.0   # сумма поля usage.cost из ответов шлюза (единицы задаёт шлюз)

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "OpenAICompatibleLLMClient":
        return cls(GatewaySettings.from_env(env_file))

    def generate_json(self, prompt: str, schema: dict, *, tier: str, trace_id: str) -> dict:
        """schema передаётся в тексте промпта самим саммаризатором; шлюзу уходит только режим json_object."""
        self._raise_if_budget_blocked()
        model = self._settings.model_by_tier.get(tier)
        if model is None:
            raise LLMGatewayError(f"для tier={tier!r} не настроена модель")
        payload = self._post(model, prompt, tier, trace_id)
        choice = payload["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("ответ модели обрезан по max_tokens")
        content = (choice.get("message") or {}).get("content") or ""
        if not content.strip():
            raise ValueError("пустой ответ модели")
        return parse_json_object(content)

    # ------------------------------------------------------------------ сеть
    def _post(self, model: str, prompt: str, tier: str, trace_id: str) -> dict:
        headers = {"Authorization": f"Bearer {self._settings.api_key.get_secret_value()}",
                   "Content-Type": "application/json"}
        for base_url in self._settings.base_urls:
            host = urlparse(base_url).netloc
            for attempt in range(self._settings.max_retries + 1):
                started = time.monotonic()
                try:
                    status, raw = self._transport(f"{base_url.rstrip('/')}/chat/completions", headers,
                                                  self._body(model, prompt), self._settings.timeout_s)
                except OSError as exc:   # таймаут и сетевые ошибки
                    log.warning("шлюз недоступен: trace_id=%s host=%s attempt=%d error=%s",
                                trace_id, host, attempt + 1, type(exc).__name__)
                    self._backoff(attempt)
                    continue
                latency_ms = int((time.monotonic() - started) * 1000)
                if status == 200:
                    payload = json.loads(raw)
                    self._account(payload, tier, model, host, latency_ms, trace_id)
                    return payload
                text = raw.decode("utf-8", errors="replace")
                if _BUDGET_MARKER in text.lower():
                    self._block_until_reset()
                    log.error("бюджет ключа исчерпан: trace_id=%s host=%s", trace_id, host)
                    raise LLMBudgetExceeded("суточный бюджет исчерпан, сброс в 3:00 МСК")
                if status == 400 and self._use_response_format and "response_format" in text:
                    self._use_response_format = False
                    log.warning("шлюз не поддерживает response_format, отключаю: trace_id=%s", trace_id)
                    return self._post(model, prompt, tier, trace_id)
                if status == 429 or status >= 500:
                    log.warning("шлюз ответил %d: trace_id=%s host=%s attempt=%d", status, trace_id, host, attempt + 1)
                    self._backoff(attempt)
                    continue
                log.error("шлюз отклонил запрос: trace_id=%s host=%s status=%d", trace_id, host, status)
                raise LLMGatewayError(f"шлюз отклонил запрос: HTTP {status} {text[:200]}")
        raise LLMGatewayError("все адреса шлюза недоступны")

    def _body(self, model: str, prompt: str) -> bytes:
        body: dict = {"model": model, "messages": [{"role": "user", "content": prompt}],
                      "temperature": self._settings.temperature, "max_tokens": self._settings.max_tokens}
        if self._settings.disable_reasoning:
            body["reasoning"] = {"enabled": False}
        if self._use_response_format:
            body["response_format"] = {"type": "json_object"}
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    def _backoff(self, attempt: int) -> None:
        if attempt < self._settings.max_retries:
            self._sleep(0.5 * 2 ** attempt)

    def _account(self, payload: dict, tier: str, model: str, host: str, latency_ms: int, trace_id: str) -> None:
        usage = payload.get("usage") or {}
        self.calls += 1
        self.prompt_tokens += usage.get("prompt_tokens", 0)
        self.completion_tokens += usage.get("completion_tokens", 0)
        self.cost += usage.get("cost") or 0.0
        log.info("ответ шлюза: trace_id=%s tier=%s model=%s host=%s latency_ms=%d prompt_tokens=%s completion_tokens=%s cost=%s",
                 trace_id, tier, model, host, latency_ms, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                 usage.get("cost"))

    # ------------------------------------------------------------------ бюджет
    def _block_until_reset(self) -> None:
        now = self._clock().astimezone(_MSK)
        reset = now.replace(hour=_BUDGET_RESET_HOUR_MSK, minute=0, second=0, microsecond=0)
        self._blocked_until = reset if now < reset else reset + timedelta(days=1)

    def _raise_if_budget_blocked(self) -> None:
        if self._blocked_until is not None and self._clock() < self._blocked_until:
            raise LLMBudgetExceeded(f"бюджет исчерпан до {self._blocked_until.isoformat()}, вызов не отправлен")
        self._blocked_until = None
