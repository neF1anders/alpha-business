"""Адаптер LLMClient к OpenAI-совместимому шлюзу. Только стандартная библиотека, без SDK."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Mapping, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator

log = logging.getLogger(__name__)

PRIMARY_BASE_URL = "https://changellenge.up.railway.app/v1"
FALLBACK_BASE_URL = "https://api.alfahack-gateway26.ru/v1"

# Маршруты по назначению вызова (`tier` = режим запроса). Первая модель основная, остальные — запасные.
# banner: сначала самая дешёвая по входным токенам (баннеры считаются пачками, вход большой).
# chat: сначала самая быстрая (пользователь ждёт ответ, SLO 4 с). Замер 2026-10-24 (72 запроса, укороченный вывод):
# deepseek p95 2.9 с, у qwen в тот же период p95 11 с. Выбрано замером на шлюзе, перепроверять при смене шлюза.
DEFAULT_ROUTES: dict[str, list[str]] = {
    "banner": ["deepseek/deepseek-v4.1-flash", "google/gemma-4-26b-a4b-it", "qwen/qwen3.6-35b-a3b"],
    "chat": ["deepseek/deepseek-v4.1-flash", "qwen/qwen3.6-35b-a3b"],
}

_FATAL_STATUSES = (401,)   # 403 шлюз отдаёт на недоступную ключу модель, смена модели помогает
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


class LLMGatewayError(Exception):
    """Шлюз недоступен или отклонил запрос. Для ветки это сбой клиента: кластер пропускается.

    `fatal=True`: смена модели не поможет (например, неверный ключ).
    """

    def __init__(self, message: str, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


class GatewaySettings(BaseModel):
    api_key: SecretStr
    base_urls: list[str] = Field(default_factory=lambda: [PRIMARY_BASE_URL, FALLBACK_BASE_URL], min_length=1)
    routes: dict[str, list[str]] = Field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_ROUTES.items()})
    timeout_s: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=1, ge=0)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=2000, gt=0)
    # Модели-«рассуждатели» иначе тратят весь max_tokens на скрытое reasoning и отдают пустой content (проверено на шлюзе).
    disable_reasoning: bool = True

    @field_validator("routes")
    @classmethod
    def _routes_are_not_empty(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        empty = sorted(route for route, models in value.items() if not models)
        if empty:
            raise ValueError(f"у маршрутов нет моделей: {empty}")
        return value

    @classmethod
    def from_env(cls, env_file: str | Path = ".env", environ: Optional[Mapping[str, str]] = None) -> "GatewaySettings":
        """Читает `.env` и переменные окружения (окружение приоритетнее). Ключ: API_KEY.

        Необязательные: LLM_BASE_URL (основной адрес), LLM_MODELS_BANNER и LLM_MODELS_CHAT
        (модели через запятую, первая основная).
        """
        values = {**_read_env_file(Path(env_file)), **(os.environ if environ is None else environ)}
        if not values.get("API_KEY"):
            raise LLMGatewayError("не найден API_KEY ни в окружении, ни в .env", fatal=True)
        settings: dict = {"api_key": values["API_KEY"]}
        if values.get("LLM_BASE_URL"):
            settings["base_urls"] = [values["LLM_BASE_URL"], FALLBACK_BASE_URL]
        routes = {route: [m.strip() for m in values[name].split(",") if m.strip()]
                  for route, name in (("banner", "LLM_MODELS_BANNER"), ("chat", "LLM_MODELS_CHAT")) if values.get(name)}
        if routes:
            settings["routes"] = {**{k: list(v) for k, v in DEFAULT_ROUTES.items()}, **routes}
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
    """Реализация LLMClient: маршрут по `tier`, запасные модели и адреса, повторы при временных сбоях."""

    def __init__(self, settings: GatewaySettings, transport: Transport = urllib_transport,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._settings = settings
        self._transport = transport
        self._sleep = sleep
        self._models_without_response_format: set[str] = set()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "OpenAICompatibleLLMClient":
        return cls(GatewaySettings.from_env(env_file))

    def generate_json(self, prompt: str, schema: dict, *, tier: str, trace_id: str) -> dict:
        """schema передаётся в тексте промпта самим саммаризатором; шлюзу уходит только режим json_object."""
        models = self._settings.routes.get(tier)
        if not models:
            raise LLMGatewayError(f"для маршрута {tier!r} не настроены модели", fatal=True)
        payload = self._post_with_model_failover(models, prompt, tier, trace_id)
        choice = payload["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("ответ модели обрезан по max_tokens")
        content = (choice.get("message") or {}).get("content") or ""
        if not content.strip():
            raise ValueError("пустой ответ модели")
        return parse_json_object(content)

    # ------------------------------------------------------------------ сеть
    def _post_with_model_failover(self, models: list[str], prompt: str, tier: str, trace_id: str) -> dict:
        last_error: Optional[LLMGatewayError] = None
        for model in models:
            try:
                return self._post(model, prompt, tier, trace_id)
            except LLMGatewayError as exc:
                if exc.fatal:
                    raise
                last_error = exc
                log.warning("модель недоступна, пробую следующую: trace_id=%s tier=%s model=%s", trace_id, tier, model)
        raise last_error or LLMGatewayError("нет доступных моделей")

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
                if status == 400 and model not in self._models_without_response_format and "response_format" in text:
                    self._models_without_response_format.add(model)
                    log.warning("модель не поддерживает response_format, отключаю: trace_id=%s model=%s", trace_id, model)
                    return self._post(model, prompt, tier, trace_id)
                if status == 429 or status >= 500:
                    log.warning("шлюз ответил %d: trace_id=%s host=%s attempt=%d", status, trace_id, host, attempt + 1)
                    self._backoff(attempt)
                    continue
                log.error("шлюз отклонил запрос: trace_id=%s host=%s model=%s status=%d", trace_id, host, model, status)
                raise LLMGatewayError(f"шлюз отклонил запрос: HTTP {status} {text[:200]}",
                                      fatal=status in _FATAL_STATUSES)
        raise LLMGatewayError(f"все адреса шлюза недоступны для модели {model}")

    def _body(self, model: str, prompt: str) -> bytes:
        body: dict = {"model": model, "messages": [{"role": "user", "content": prompt}],
                      "temperature": self._settings.temperature, "max_tokens": self._settings.max_tokens}
        if self._settings.disable_reasoning:
            body["reasoning"] = {"enabled": False}
        if model not in self._models_without_response_format:
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
        log.info("ответ шлюза: trace_id=%s tier=%s model=%s host=%s latency_ms=%d prompt_tokens=%s completion_tokens=%s",
                 trace_id, tier, model, host, latency_ms, usage.get("prompt_tokens"), usage.get("completion_tokens"))
