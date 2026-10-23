"""Адаптер LLM: настройки, разбор ответа, повторы, запасной адрес, бюджет. Сеть не используется."""
import json
from datetime import datetime, timezone

import pytest
from summary_branch.llm_gateway import (
    FALLBACK_BASE_URL, PRIMARY_BASE_URL, GatewaySettings, LLMBudgetExceeded, LLMGatewayError,
    OpenAICompatibleLLMClient, parse_json_object,
)

KEY = "sk-test-secret"
DRAFT = {"headline": "h", "claims": []}


def ok(content, finish="stop"):
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.5}}
    return 200, json.dumps(body).encode()


class ScriptedTransport:
    """Отдаёт ответы по очереди; элемент-исключение выбрасывается."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, url, headers, body, timeout):
        self.requests.append((url, headers, json.loads(body)))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client(*responses, now=None, **settings):
    transport = ScriptedTransport(*responses)
    clock = (lambda: now) if now else (lambda: datetime.now(timezone.utc))
    return OpenAICompatibleLLMClient(GatewaySettings(api_key=KEY, **settings), transport, sleep=lambda s: None,
                                     clock=clock), transport


def call(c, tier="full"):
    return c.generate_json("prompt", {}, tier=tier, trace_id="t1")


def test_success_sends_model_by_tier_and_counts_usage():
    c, transport = client(ok(json.dumps(DRAFT)), ok(json.dumps(DRAFT)))
    assert call(c, "full") == DRAFT and call(c, "lite") == DRAFT
    (url, headers, body), (_, _, lite_body) = transport.requests
    assert url == f"{PRIMARY_BASE_URL}/chat/completions"
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert body["model"] == "deepseek/deepseek-v4.1-flash" and lite_body["model"] == "z-ai/glm-5.3-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["reasoning"] == {"enabled": False}
    assert (c.calls, c.prompt_tokens, c.completion_tokens, c.cost) == (2, 200, 40, 1.0)


@pytest.mark.parametrize("content", [
    json.dumps(DRAFT), "```json\n" + json.dumps(DRAFT) + "\n```",
    "<think>размышляю {не json}</think>" + json.dumps(DRAFT), "Вот ответ: " + json.dumps(DRAFT) + " Готово."])
def test_parse_json_object_tolerates_wrappers(content):
    assert parse_json_object(content) == DRAFT


@pytest.mark.parametrize("content", ["не json", "[1, 2]", ""])
def test_unparseable_content_raises_value_error_for_summarizer_retry(content):
    c, _ = client(ok(content))
    with pytest.raises(ValueError):
        call(c)


def test_truncated_answer_is_value_error():
    c, _ = client(ok('{"headline": "обор', finish="length"))
    with pytest.raises(ValueError):
        call(c)


def test_retries_server_error_then_succeeds_on_same_host():
    c, transport = client((503, b"busy"), ok(json.dumps(DRAFT)))
    assert call(c) == DRAFT
    assert [r[0] for r in transport.requests] == [f"{PRIMARY_BASE_URL}/chat/completions"] * 2


def test_falls_back_to_second_host_after_network_errors():
    c, transport = client(TimeoutError(), ConnectionError(), ok(json.dumps(DRAFT)))
    assert call(c) == DRAFT
    assert transport.requests[-1][0] == f"{FALLBACK_BASE_URL}/chat/completions"


def test_all_hosts_down_raises_gateway_error():
    c, _ = client(*[TimeoutError()] * 4)
    with pytest.raises(LLMGatewayError, match="недоступны"):
        call(c)


def test_client_error_is_not_retried_and_hides_key():
    c, transport = client((401, b'{"error": "bad key"}'))
    with pytest.raises(LLMGatewayError) as exc:
        call(c)
    assert len(transport.requests) == 1 and KEY not in str(exc.value)


def test_budget_exceeded_blocks_further_calls_until_reset_msk():
    evening = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)            # 21:00 МСК
    c, transport = client((429, b'{"error": "Budget has been exceeded"}'), now=evening)
    with pytest.raises(LLMBudgetExceeded):
        call(c)
    with pytest.raises(LLMBudgetExceeded, match="не отправлен"):
        call(c)
    assert len(transport.requests) == 1                                      # второй вызов в сеть не ушёл
    assert c._blocked_until == datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)             # 03:00 МСК
    c._clock = lambda: datetime(2026, 10, 24, 0, 0, 1, tzinfo=timezone.utc)                   # сразу после сброса
    c._transport = ScriptedTransport(ok(json.dumps(DRAFT)))
    assert call(c) == DRAFT


def test_response_format_is_dropped_once_gateway_rejects_it():
    c, transport = client((400, b'{"error": "unsupported response_format"}'), ok(json.dumps(DRAFT)), ok(json.dumps(DRAFT)))
    assert call(c) == DRAFT and call(c) == DRAFT
    assert "response_format" in transport.requests[0][2] and "response_format" not in transport.requests[1][2]
    assert "response_format" not in transport.requests[2][2]


def test_unknown_tier_is_gateway_error():
    c, _ = client()
    with pytest.raises(LLMGatewayError):
        call(c, "ultra")


def test_settings_reject_unknown_model_and_hide_key_in_repr():
    with pytest.raises(ValueError):
        GatewaySettings(api_key=KEY, model_by_tier={"full": "gpt-imaginary"})
    assert KEY not in repr(GatewaySettings(api_key=KEY))


def test_settings_from_env_file_tolerates_spaces_and_environment_overrides(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('# комментарий\nAPI_KEY = "sk-from-file"\nLLM_MODEL_LITE=qwen/qwen3.6-35b-a3b\n', encoding="utf-8")
    settings = GatewaySettings.from_env(env_file, environ={})
    assert settings.api_key.get_secret_value() == "sk-from-file"
    assert settings.model_by_tier == {"full": "deepseek/deepseek-v4.1-flash", "lite": "qwen/qwen3.6-35b-a3b"}
    overridden = GatewaySettings.from_env(env_file, environ={"API_KEY": "sk-env", "LLM_BASE_URL": "http://localhost/v1"})
    assert overridden.api_key.get_secret_value() == "sk-env" and overridden.base_urls[0] == "http://localhost/v1"


def test_settings_without_key_raise(tmp_path):
    with pytest.raises(LLMGatewayError):
        GatewaySettings.from_env(tmp_path / "missing.env", environ={})


def test_gateway_errors_map_to_summarization_failed_in_summarizer():
    from factories import make_req
    from summary_branch.errors import SummarizationFailed
    from summary_branch.summarizer import LlmSummarizer
    c, _ = client(*[TimeoutError()] * 4)
    with pytest.raises(SummarizationFailed):
        LlmSummarizer(c).summarize(make_req())
