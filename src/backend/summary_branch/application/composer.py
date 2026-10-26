"""S4+S7: личный баннер компании из общего содержимого кластера и персонального текста."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Callable

from ..config import BranchConfig
from ..domain.models import Banner, ClusterSummary, CompanyContext, Confidence, ContentKey, Decision, utcnow
from .personal import PersonalText


def make_banner_id(company_id: str, key: ContentKey) -> str:
    """Детерминированный id: повторный прогон перезаписывает баннер, а не плодит дубли."""
    raw = "|".join((company_id, key.cluster_id, key.prompt_version))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class DefaultBannerComposer:
    def __init__(self, config: BranchConfig | None = None, clock: Callable[[], datetime] = utcnow) -> None:
        self._config = config or BranchConfig()
        self._clock = clock

    def compose(self, seg: ClusterSummary, company: CompanyContext, confidence: Confidence,
                personal: PersonalText) -> Banner:
        if confidence.decision == Decision.DROP:
            raise ValueError("баннер для решения DROP не создаётся")
        raw = confidence.decision == Decision.SHOW_RAW_NEWS   # при низкой уверенности интерпретацию не показываем
        now = self._clock()
        return Banner(
            banner_id=make_banner_id(company.company_id, seg.key), company_id=company.company_id, key=seg.key,
            topic=seg.topic, severity=seg.severity, headline="" if raw else personal.headline,
            for_you="" if raw else personal.for_you, confidence=confidence.score, decision=confidence.decision,
            created_at=now, expires_at=now + timedelta(hours=self._config.ttl_hours(seg.topic)),
        )
