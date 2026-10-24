"""S2–S3b с проверкой: генерация саммари, привязка доказательств, сигналы, повтор генерации при невалидном результате."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .config import BranchConfig
from .errors import BudgetExhausted, SummarizationFailed
from .interfaces import ContentScorer, EvidenceBinder, Summarizer
from .models import BudgetTier, Claim, ContentSignals, Mode, SourceRef, SummaryDraft, SummaryRequest

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeneratedContent:
    draft: SummaryDraft
    claims: list[Claim]
    sources: list[SourceRef]
    signals: ContentSignals


class ValidatedGenerator:
    """Валидатор здесь, а не у оркестратора: ветка сама решает, годится ли результат или нужна ещё одна генерация."""

    def __init__(self, summarizer: Summarizer, binder: EvidenceBinder, content_scorer: ContentScorer,
                 config: BranchConfig | None = None) -> None:
        self._summarizer = summarizer
        self._binder = binder
        self._content_scorer = content_scorer
        self._config = config or BranchConfig()

    def is_valid(self, signals: ContentSignals) -> bool:
        """Валидно: структура целая, числа подтверждены источником, факты обоснованы."""
        return (signals.structure_ok and not signals.numeric_miss
                and signals.grounding >= self._config.min_grounding)

    def generate(self, req: SummaryRequest) -> GeneratedContent:
        """Лучший из попыток. Если ни одна не валидна, отдаёт наименее плохую; ошибка первой попытки пробрасывается.

        Повторы только при полном бюджете: lite и cached_only живут без них.
        """
        attempts = self._attempts(req)
        best: Optional[GeneratedContent] = None
        for attempt in range(1, attempts + 1):
            try:
                candidate = self._attempt(req)
            except (SummarizationFailed, BudgetExhausted) as exc:
                if best is None:
                    raise
                log.warning("повтор генерации сорвался: trace_id=%s attempt=%d/%d reason=%s",
                            req.trace_id, attempt, attempts, type(exc).__name__)
                break
            if self.is_valid(candidate.signals):
                return candidate
            log.info("саммари не прошло проверку: trace_id=%s attempt=%d/%d structure_ok=%s numeric_miss=%s "
                     "grounding=%.3f", req.trace_id, attempt, attempts, candidate.signals.structure_ok,
                     candidate.signals.numeric_miss, candidate.signals.grounding)
            if best is None or self._rank(candidate) > self._rank(best):
                best = candidate
        assert best is not None   # первая попытка либо задаёт best, либо возвращает результат, либо бросает
        return best

    def _attempts(self, req: SummaryRequest) -> int:
        if req.budget != BudgetTier.FULL:
            return 1
        return self._config.chat_generation_attempts if req.mode == Mode.CHAT else self._config.max_generation_attempts

    def _attempt(self, req: SummaryRequest) -> GeneratedContent:
        draft = self._summarizer.summarize(req)
        claims, sources = self._binder.bind(draft, req)
        signals = self._content_scorer.score(req, draft, claims, sources)
        return GeneratedContent(draft=draft, claims=claims, sources=sources, signals=signals)

    @staticmethod
    def _rank(content: GeneratedContent) -> tuple[bool, bool, float]:
        signals = content.signals
        return signals.structure_ok, not signals.numeric_miss, signals.grounding
