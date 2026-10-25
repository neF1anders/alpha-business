"""Детерминированный LLM-клиент для тестов, демо и запуска без ключа шлюза (`LLM_MODE=fake`)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Optional

from ..application.personal import FACTS_HEADLINE_RE, PATH_LINE_RE, PERSONAL_BEGIN
from ..application.summarizer import BANK_BLOCK_RE, DATA_BEGIN, NEWS_BLOCK_RE, QUESTION_BLOCK_RE
from ..domain.personalization import news_sectors

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_NEWS_TAGS_RE = re.compile(r'<<<NEWS id="(?P<id>[^"]*)"[^\n]*tags="(?P<tags>[^"]*)"')
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
    """Строит ответ по данным из промпта. broken_responses: сколько первых вызовов вернут битый JSON.

    tamper меняет черновик кластера или чата, personal_tamper — персональный ответ.
    """

    def __init__(self, broken_responses: int = 0, tamper: Optional[Callable[[dict], dict]] = None,
                 personal_tamper: Optional[Callable[[dict], dict]] = None) -> None:
        self.calls: list[LlmCall] = []
        self._broken_left = broken_responses
        self._tamper = tamper
        self._personal_tamper = personal_tamper

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def calls_of(self, kind: str) -> list[LlmCall]:
        """kind: personal — персональный шаг, summary — разбор кластера и чат."""
        return [c for c in self.calls if (PERSONAL_BEGIN in c.prompt) == (kind == "personal")]

    def generate_json(self, prompt: str, schema: dict, *, tier: str, trace_id: str) -> dict:
        self.calls.append(LlmCall(prompt, tier, trace_id))
        if self._broken_left > 0:
            self._broken_left -= 1
            return json.loads('{"headline": "оборванный ответ')   # бросает JSONDecodeError, как шлюз при битом JSON
        if PERSONAL_BEGIN in prompt:
            draft = self._personal_from_prompt(prompt)
            return self._personal_tamper(draft) if self._personal_tamper else draft
        draft = self._draft_from_prompt(prompt)
        return self._tamper(draft) if self._tamper else draft

    @staticmethod
    def _personal_from_prompt(prompt: str) -> dict:
        data = prompt.split(DATA_BEGIN, 1)[1]
        paths = [m.groupdict() for m in PATH_LINE_RE.finditer(data)]
        headline = FACTS_HEADLINE_RE.search(data)
        best = paths[0]
        hedge = "может сказаться на вас" if best["link"] == "hypothesis" else "касается вас напрямую"
        return {"headline": (headline.group("headline") if headline else "Новость для вас")[:60],
                "for_you": f"{best['explanation'][:1].upper()}{best['explanation'][1:]}: новость {hedge}.",
                "path_ids": [best["id"]]}

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
        tags = _NEWS_TAGS_RE.search(data)
        sectors = sorted(news_sectors(tags.group("tags").split(",") if tags and tags.group("tags") else []))
        return {
            "headline": lead["title"][:60],
            "for_you_template": "У вас {feature}: учтите это при планировании",
            "for_you_fallback": "Учтите это при планировании",
            "topic": topic,
            "severity": "attention" if topic in ("rate", "fx") else "watch",
            "claims": claims,
            "follow_ups": ["Что это значит для моих кредитов?"] if question else [],
            "impacts": [] if question else [
                {"division": division, "direction": "negative", "mechanism": "Издержки отрасли растут",
                 "evidence_ids": [lead["id"]]} for division in sectors],
        }
