"""S2–S3b с проверкой: генерация саммари, привязка доказательств, сигналы, повтор генерации при невалидном результате."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ..config import BranchConfig
from ..domain.errors import BudgetExhausted, SummarizationFailed
from ..domain.ports import ContentScorer, EvidenceBinder, Summarizer
from ..domain.models import BudgetTier, Claim, ClaimType, ContentSignals, Mode, SourceRef, SummaryDraft, SummaryRequest
from ..domain.scoring import CONTENT_FLAG_REASONS, claim_problems, passes_validation

log = logging.getLogger(__name__)

MAX_PROBLEMS = 5   # длиннее список только раздувает промпт


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
        """Валидно: структура целая, числа и слова утверждений опираются на источник, факты обоснованы."""
        return passes_validation(signals, self._config.min_grounding)

    def generate(self, req: SummaryRequest) -> GeneratedContent:
        """Лучший из попыток. Если ни одна не валидна, отдаёт наименее плохую; ошибка первой попытки пробрасывается.

        Повторы только при полном бюджете: lite и cached_only живут без них.
        """
        attempts = self._attempts(req)
        best: Optional[GeneratedContent] = None
        attempt_req = req
        for attempt in range(1, attempts + 1):
            try:
                candidate = self._attempt(attempt_req)
            except (SummarizationFailed, BudgetExhausted) as exc:
                if best is None:
                    raise
                log.warning("повтор генерации сорвался: trace_id=%s attempt=%d/%d reason=%s",
                            req.trace_id, attempt, attempts, type(exc).__name__)
                break
            if self.is_valid(candidate.signals):
                return candidate
            log.info("саммари не прошло проверку: trace_id=%s attempt=%d/%d structure_ok=%s numeric_miss=%s "
                     "unsupported_claim=%s grounding=%.3f", req.trace_id, attempt, attempts,
                     candidate.signals.structure_ok, candidate.signals.numeric_miss,
                     candidate.signals.unsupported_claim, candidate.signals.grounding)
            attempt_req = req.model_copy(update={"previous_problems": self._problems(candidate, req)})
            if best is None or self._rank(candidate) > self._rank(best):
                best = candidate
        assert best is not None   # первая попытка либо задаёт best, либо возвращает результат, либо бросает
        return best

    @staticmethod
    def _problems(content: GeneratedContent, req: SummaryRequest) -> list[str]:
        """Что не так с неудачной попыткой: модель получает это в следующем запросе и исправляет именно это."""
        evidence = {n.news_id: f"{n.title} {n.text}" for n in req.news}
        evidence.update({m.doc_id: m.claim_text for m in req.bank_materials})
        problems: list[str] = []
        for claim in content.claims:
            if claim.type not in (ClaimType.FACT, ClaimType.BANK):
                continue
            source = " ".join([e.quote for e in claim.evidence] + [evidence.get(e.ref_id, "") for e in claim.evidence])
            problems += [f"«{claim.text[:80]}»: {problem}" for problem in claim_problems(claim, source)]
        signals = content.signals
        if signals.ungrounded_inference:
            problems.append("вывод без опоры: укажи evidence_ids или убери вывод")
        if not signals.structure_ok:
            problems.append("в ответе нет проверяемого факта с источником")
        return problems[:MAX_PROBLEMS]

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
    def _rank(content: GeneratedContent) -> tuple[bool, bool, bool, bool, float]:
        signals = content.signals
        meaning_ok = not any(getattr(signals, flag) for flag, _ in CONTENT_FLAG_REASONS)
        return signals.structure_ok, not signals.numeric_miss, not signals.unsupported_claim, meaning_ok, signals.grounding
