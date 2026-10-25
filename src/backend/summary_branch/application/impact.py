"""S3c. Проверка карты влияния из ответа модели: отрасли только из ОКВЭД, механизм опирается на новость."""
from __future__ import annotations

import logging
import re

from ..domain.models import SectorImpact, SummaryDraft, SummaryRequest
from ..domain.scoring import _numbers

log = logging.getLogger(__name__)

MAX_IMPACTS = 6
MECHANISM_MAX = 160
_DIVISION = re.compile(r"^\d{2}$")


def bind_impacts(draft: SummaryDraft, req: SummaryRequest) -> list[SectorImpact]:
    """Оставляет элементы с корректным разделом ОКВЭД, ссылкой на новость запроса и без чисел, которых нет в источнике."""
    news_by_id = {n.news_id: n for n in req.news}
    impacts: dict[str, SectorImpact] = {}
    dropped = 0
    for item in draft.impacts:
        division = item.division.strip().zfill(2)
        evidence = [ref for ref in dict.fromkeys(item.evidence_ids) if ref in news_by_id]
        source_text = " ".join(f"{news_by_id[ref].title} {news_by_id[ref].text}" for ref in evidence)
        mechanism = item.mechanism.strip()[:MECHANISM_MAX]
        if not _DIVISION.match(division) or not evidence or not mechanism or not _numbers(mechanism) <= _numbers(source_text):
            dropped += 1
            continue
        impacts.setdefault(division, SectorImpact(division=division, direction=item.direction, mechanism=mechanism,
                                                  evidence_ids=evidence))
    if dropped:
        log.info("карта влияния: отброшено trace_id=%s dropped=%d kept=%d", req.trace_id, dropped, len(impacts))
    return list(impacts.values())[:MAX_IMPACTS]


def normalized_countries(draft: SummaryDraft) -> list[str]:
    return list(dict.fromkeys(c.strip().upper() for c in draft.countries if c.strip()))


def normalized_regions(draft: SummaryDraft) -> list[str]:
    return list(dict.fromkeys(r.strip() for r in draft.regions if r.strip()))
