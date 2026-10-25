"""Заглушки для тестов и демо: детерминированный LLM-клиент и статический справочник компаний."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Optional

from ..domain.models import CompanyContext
from ..application.summarizer import BANK_BLOCK_RE, DATA_BEGIN, NEWS_BLOCK_RE, QUESTION_BLOCK_RE

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_TOPIC_KEYWORDS = (
    ("rate", ("ставк",)),
    ("fx", ("курс", "доллар", "юан", "валют")),
    ("labor", ("зарплат", "труд", "безработ")),
    ("demand", ("спрос", "продаж")),
)


@dataclass(frozen=True)
class LlmCall:
    prompt: str
    tier: str
    trace_id: str


class FakeLLMClient:
    """Строит SummaryDraft по данным из промпта. broken_responses: сколько первых вызовов вернут битый JSON."""

    def __init__(self, broken_responses: int = 0, tamper: Optional[Callable[[dict], dict]] = None) -> None:
        self.calls: list[LlmCall] = []
        self._broken_left = broken_responses
        self._tamper = tamper

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def generate_json(self, prompt: str, schema: dict, *, tier: str, trace_id: str) -> dict:
        self.calls.append(LlmCall(prompt, tier, trace_id))
        if self._broken_left > 0:
            self._broken_left -= 1
            return json.loads('{"headline": "оборванный ответ')   # бросает JSONDecodeError, как шлюз при битом JSON
        draft = self._draft_from_prompt(prompt)
        return self._tamper(draft) if self._tamper else draft

    @staticmethod
    def _draft_from_prompt(prompt: str) -> dict:
        data = prompt.split(DATA_BEGIN, 1)[1]
        news = [m.groupdict() for m in NEWS_BLOCK_RE.finditer(data)]
        banks = [m.groupdict() for m in BANK_BLOCK_RE.finditer(data)]
        question = QUESTION_BLOCK_RE.search(data)
        lead = news[0]
        lowered = f"{lead['title']} {lead['text']}".lower()
        topic = next((name for name, words in _TOPIC_KEYWORDS if any(w in lowered for w in words)), "other")
        sentences = [s for s in _SENTENCE_SPLIT.split(lead["text"]) if s]
        key_sentence = next((s for s in sentences if re.search(r"\d", s)), sentences[0] if sentences else lead["title"])
        claims = [{"type": "fact", "text": key_sentence.rstrip("."), "evidence_ids": [lead["id"]]}]
        claims += [{"type": "bank", "text": b["claim"].rstrip("."), "evidence_ids": [b["id"]]} for b in banks[:1]]
        claims.append({"type": "model", "text": "Нагрузка по кредитным продуктам сегмента может сохраниться",
                       "evidence_ids": [lead["id"]]})
        return {
            "headline": lead["title"][:60],
            "for_you_template": "У вас {feature}: учтите это при планировании",
            "for_you_fallback": "Учтите это при планировании",
            "topic": topic,
            "severity": "attention" if topic in ("rate", "fx") else "watch",
            "claims": claims,
            "follow_ups": ["Что это значит для моих кредитов?"] if question else [],
        }


class StaticDirectory:
    def __init__(self, companies: list[CompanyContext]) -> None:
        self._companies = list(companies)

    def active_in_segment(self, segment_id: str) -> list[CompanyContext]:
        return [c for c in self._companies if c.segment_id == segment_id]

    def get(self, company_id: str) -> Optional[CompanyContext]:
        return next((c for c in self._companies if c.company_id == company_id), None)
