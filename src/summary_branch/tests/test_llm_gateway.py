"""Адаптер LLM: настройки, маршруты, разбор ответа, повторы, запасные адреса и модели. Сеть не используется."""
import json

import pytest
from summary_branch.llm_gateway import (
    DEFAULT_ROUTES, FALLBACK_BASE_URL, PRIMARY_BASE_URL, GatewaySettings, LLMGatewayError,
    OpenAICompatibleLLMClient, parse_json_object,
)

KEY = "sk-test-secret"
DRAFT = {"headline": "h", "claims": []}


def ok(content, finish="stop"):
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
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


def client(*responses, **settings):
    transport = ScriptedTransport(*responses)
    return OpenAICompatibleLLMClient(GatewaySettings(api_key=KEY, **settings), transport, sleep=lambda s: None), transport


def call(c, tier="banner"):
    return c.generate_json("prompt", {}, tier=tier, trace_id="t1")


def models_used(transport):
    return [r[2]["model"] for r in transport.requests]


def test_routes_pick_cheap_model_for_banner_and_fast_model_for_chat():
    c, transport = client(ok(json.dumps(DRAFT)), ok(json.dumps(DRAFT)))
    assert call(c, "banner") == DRAFT and call(c, "chat") == DRAFT
    (url, headers, body), (_, _, chat_body) = transport.requests
    assert url == f"{PRIMARY_BASE_URL}/chat/completions"
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert body["model"] == DEFAULT_ROUTES["banner"][0] == "deepseek/deepseek-v4.1-flash"
    assert chat_body["model"] == DEFAULT_ROUTES["chat"][0] == "qwen/qwen3.6-35b-a3b"
    assert body["response_format"] == {"type": "json_object"} and body["reasoning"] == {"enabled": False}
    assert (c.calls, c.prompt_tokens, c.completion_tokens) == (2, 200, 40)


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


def test_retries_server_error_then_succeeds_on_same_host_and_model():
    c, transport = client((503, b"busy"), ok(json.dumps(DRAFT)))
    assert call(c) == DRAFT
    assert [r[0] for r in transport.requests] == [f"{PRIMARY_BASE_URL}/chat/completions"] * 2
    assert len(set(models_used(transport))) == 1


def test_falls_back_to_second_host_before_changing_model():
    c, transport = client(TimeoutError(), ConnectionError(), ok(json.dumps(DRAFT)))
    assert call(c) == DRAFT
    assert transport.requests[-1][0] == f"{FALLBACK_BASE_URL}/chat/completions"
    assert len(set(models_used(transport))) == 1


def test_switches_to_next_model_when_hosts_are_down_for_the_first():
    c, transport = client(*[TimeoutError()] * 4, ok(json.dumps(DRAFT)))
    assert call(c) == DRAFT
    assert models_used(transport)[-1] == DEFAULT_ROUTES["banner"][1]


def test_all_models_down_raises_gateway_error():
    c, _ = client(*[TimeoutError()] * 12)
    with pytest.raises(LLMGatewayError, match="недоступны"):
        call(c)


def test_auth_error_is_fatal_and_does_not_try_other_models_or_leak_key():
    c, transport = client((401, b'{"error": "bad key"}'))
    with pytest.raises(LLMGatewayError) as exc:
        call(c)
    assert exc.value.fatal and len(transport.requests) == 1 and KEY not in str(exc.value)


@pytest.mark.parametrize("status", [403, 404])
def test_model_access_error_switches_model(status):
    c, transport = client((status, b"model not allowed"), ok(json.dumps(DRAFT)))
    assert call(c) == DRAFT
    assert models_used(transport) == DEFAULT_ROUTES["banner"][:2]


def test_response_format_is_dropped_per_model_once_rejected():
    c, transport = client((400, b'{"error": "unsupported response_format"}'), ok(json.dumps(DRAFT)),
                          ok(json.dumps(DRAFT)), ok(json.dumps(DRAFT)))
    assert call(c) == DRAFT and call(c) == DRAFT
    assert "response_format" in transport.requests[0][2]
    assert "response_format" not in transport.requests[1][2] and "response_format" not in transport.requests[2][2]
    assert call(c, "chat") == DRAFT
    assert "response_format" in transport.requests[3][2]          # у другой модели флаг остался


def test_unknown_route_is_fatal_gateway_error():
    c, _ = client()
    with pytest.raises(LLMGatewayError) as exc:
        call(c, "ultra")
    assert exc.value.fatal


def test_settings_reject_empty_route_and_hide_key_in_repr():
    with pytest.raises(ValueError):
        GatewaySettings(api_key=KEY, routes={"banner": []})
    assert KEY not in repr(GatewaySettings(api_key=KEY))


def test_settings_from_env_file_tolerates_spaces_and_environment_overrides(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('# комментарий\nAPI_KEY = "sk-from-file"\nLLM_MODELS_CHAT= a/one , b/two\n', encoding="utf-8")
    settings = GatewaySettings.from_env(env_file, environ={})
    assert settings.api_key.get_secret_value() == "sk-from-file"
    assert settings.routes == {"banner": DEFAULT_ROUTES["banner"], "chat": ["a/one", "b/two"]}
    overridden = GatewaySettings.from_env(env_file, environ={"API_KEY": "sk-env", "LLM_BASE_URL": "http://localhost/v1"})
    assert overridden.api_key.get_secret_value() == "sk-env" and overridden.base_urls[0] == "http://localhost/v1"


def test_settings_without_key_raise(tmp_path):
    with pytest.raises(LLMGatewayError):
        GatewaySettings.from_env(tmp_path / "missing.env", environ={})


def test_gateway_errors_map_to_summarization_failed_in_summarizer():
    from factories import make_req
    from summary_branch.errors import SummarizationFailed
    from summary_branch.summarizer import LlmSummarizer
    c, _ = client(*[TimeoutError()] * 12)
    with pytest.raises(SummarizationFailed):
        LlmSummarizer(c).summarize(make_req())
