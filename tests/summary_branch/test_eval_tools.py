"""Тесты вспомогательных инструментов прогонов (scripts/eval), без сети."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "eval"))

from budget import BudgetGuard, BudgetStop, allowed_calls, parse_key_info  # noqa: E402
from stats import percentile, wilson  # noqa: E402


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([5], 90) == 5
    with pytest.raises(ValueError):
        percentile([], 50)


def test_wilson_interval_is_sane():
    low, high = wilson(0, 50)
    assert low == 0.0 and 0.05 < high < 0.1
    assert wilson(0, 0) == (0.0, 1.0)


def test_parse_key_info_accepts_nested_and_flat_payloads():
    nested = parse_key_info({"key": "x", "info": {"spend": 3.4, "max_budget": 500}})
    assert nested.spend == 3.4 and nested.remaining == 496.6
    assert parse_key_info({"spend": 1, "max_budget": None}).remaining is None
    with pytest.raises(ValueError):
        parse_key_info({"info": {}})


def fake_fetch(spends):
    calls = iter(spends)

    def fetch(url, headers):
        assert url == "https://gw.example/key/info" and headers["Authorization"] == "Bearer secret"
        return 200, json.dumps({"info": {"spend": next(calls), "max_budget": 500}}).encode()
    return fetch


def test_guard_stops_at_ceiling_and_does_not_leak_key():
    guard = BudgetGuard("https://gw.example/v1", "secret", ceiling=10, fetch=fake_fetch([100, 105, 111]))
    assert guard.check().spend == 105
    with pytest.raises(BudgetStop) as stop:
        guard.check()
    assert "secret" not in str(stop.value)


def test_guard_stops_when_remaining_is_low():
    guard = BudgetGuard("https://gw.example/v1", "secret", ceiling=1000, min_remaining=50,
                        fetch=fake_fetch([100, 460]))
    with pytest.raises(BudgetStop):
        guard.check()


def test_allowed_calls():
    assert allowed_calls(0.05, 80, 250) == 250
    assert allowed_calls(1.0, 80, 250) == 80
    assert allowed_calls(0, 80, 250) == 250
