"""S4+S7: личный баннер компании из общего содержимого сегмента."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Callable

from .config import BranchConfig
from .models import Banner, CompanyContext, Confidence, ContentKey, Decision, SegmentSummary, utcnow
from .scoring import render_for_you


def make_banner_id(company_id: str, key: ContentKey) -> str:
    """Детерминированный id: повторный прогон перезаписывает баннер, а не плодит дубли."""
    raw = "|".join((company_id, key.cluster_id, key.segment_id, key.prompt_version))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class DefaultBannerComposer:
    def __init__(self, config: BranchConfig | None = None, clock: Callable[[], datetime] = utcnow) -> None:
        self._config = config or BranchConfig()
        self._clock = clock

    def compose(self, seg: SegmentSummary, company: CompanyContext, confidence: Confidence) -> Banner:
        if confidence.decision == Decision.DROP:
            raise ValueError("баннер для решения DROP не создаётся")
        if confidence.decision == Decision.SHOW_RAW_NEWS:
            for_you = ""  # при низкой уверенности интерпретацию не показываем
        else:
            claims_text = " ".join(c.text for c in seg.claims)
            for_you = render_for_you(seg.for_you_template, seg.for_you_fallback, company, claims_text)
        now = self._clock()
        return Banner(
            banner_id=make_banner_id(company.company_id, seg.key), company_id=company.company_id, key=seg.key,
            topic=seg.topic, severity=seg.severity, for_you=for_you, confidence=confidence.score,
            decision=confidence.decision, created_at=now,
            expires_at=now + timedelta(hours=self._config.ttl_hours(seg.topic)),
        )
