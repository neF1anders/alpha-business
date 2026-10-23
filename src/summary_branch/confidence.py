"""S3b и S5: тонкие адаптеры над scoring.py. Калибратор и NLI подключаются позже необязательными параметрами."""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from .models import (
    Claim, CompanyContext, Confidence, ContentSignals, SegmentSummary, SourceRef, SummaryDraft, SummaryRequest,
)
from .scoring import company_confidence, content_signals


class DefaultContentScorer:
    """Сигналы сегмента (не зависят от компании). Заполняет Claim.grounding."""

    def __init__(self, nli: Optional[Callable[[str, str], float]] = None) -> None:
        self._nli = nli

    def score(self, req: SummaryRequest, draft: SummaryDraft, claims: list[Claim],
              sources: list[SourceRef]) -> ContentSignals:
        return content_signals(req, draft, claims, sources, nli=self._nli)


class DefaultConfidenceScorer:
    def __init__(self, calibrator: Optional[Callable[[float], float]] = None) -> None:
        self._calibrator = calibrator

    def score(self, seg: SegmentSummary, company: CompanyContext, now: Optional[datetime] = None) -> Confidence:
        return company_confidence(seg.signals, seg.lead, seg.topic, company, has_sources=bool(seg.sources),
                                  now=now, calibrator=self._calibrator)
