"""S3. Детерминированная привязка доказательств: ссылки -> цитаты, чистка утверждений, сбор источников."""
from __future__ import annotations

import logging
import re

from ..config import BranchConfig
from ..domain.models import (
    Claim, ClaimType, EvidenceRef, SourceKind, SourceRef, SummaryDraft, SummaryRequest,
)
from ..domain.scoring import _NUM, _numbers, _stems

log = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_SOURCE_ORDER = {SourceKind.PRIMARY: 0, SourceKind.BANK: 1, SourceKind.MEDIA: 2}
_MUST_HAVE_EVIDENCE = (ClaimType.FACT, ClaimType.BANK)


def _normalize(text: str) -> str:
    return _NON_WORD.sub(" ", text.casefold()).strip()


def _overlap_score(claim_text: str, sentence: str) -> float:
    """Числа весят больше слов: цитата прежде всего должна подтверждать цифру."""
    claim_numbers, claim_stems = _numbers(claim_text), _stems(claim_text)
    number_hits = len(claim_numbers & _numbers(sentence))
    stem_share = len(claim_stems & _stems(sentence)) / len(claim_stems) if claim_stems else 0.0
    return number_hits * 10 + stem_share


def _clip_quote(sentence: str, claim_text: str, limit: int) -> str:
    """Обрезает по лимиту так, чтобы число из утверждения не потерялось в хвосте."""
    sentence = sentence.strip()
    if len(sentence) <= limit:
        return sentence
    claim_numbers = _numbers(claim_text)
    anchor = next(
        (m.start() for m in _NUM.finditer(sentence) if _numbers(m.group()) & claim_numbers), 0,
    )
    start = max(0, min(anchor - limit // 4, len(sentence) - limit))
    return sentence[start:start + limit].strip()


def _best_quote(claim_text: str, source_text: str, limit: int) -> str:
    sentences = [s for s in _SENTENCE_SPLIT.split(source_text.strip()) if s]
    if not sentences:
        return ""
    best = max(sentences, key=lambda s: _overlap_score(claim_text, s))
    return _clip_quote(best, claim_text, limit)


class DefaultEvidenceBinder:
    def __init__(self, config: BranchConfig | None = None) -> None:
        self._config = config or BranchConfig()

    def bind(self, draft: SummaryDraft, req: SummaryRequest) -> tuple[list[Claim], list[SourceRef]]:
        news_by_id = {n.news_id: n for n in req.news}
        docs_by_id = {m.doc_id: m for m in req.bank_materials}
        limit = self._config.quote_max_chars

        claims: list[Claim] = []
        seen_texts: dict[str, Claim] = {}
        dropped = 0
        for draft_claim in draft.claims:
            evidence = self._build_evidence(draft_claim.text, draft_claim.evidence_ids,
                                            news_by_id, docs_by_id, limit)
            if draft_claim.type in _MUST_HAVE_EVIDENCE and not evidence:
                dropped += 1
                continue
            normalized = _normalize(draft_claim.text)
            twin = seen_texts.get(normalized)
            if twin is not None:
                known = {e.ref_id for e in twin.evidence}
                twin.evidence.extend(e for e in evidence if e.ref_id not in known)
                continue
            claim = Claim(type=draft_claim.type, text=draft_claim.text, evidence=evidence)
            seen_texts[normalized] = claim
            claims.append(claim)

        if dropped:
            log.warning("утверждения без опоры отброшены: trace_id=%s dropped=%d kept=%d",
                        req.trace_id, dropped, len(claims))
        return claims, self._collect_sources(claims, news_by_id, docs_by_id)

    @staticmethod
    def _build_evidence(claim_text, evidence_ids, news_by_id, docs_by_id, limit) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        for ref_id in dict.fromkeys(evidence_ids):
            if ref_id in news_by_id:
                news = news_by_id[ref_id]
                source_text = f"{news.title}. {news.text}"
            elif ref_id in docs_by_id:
                source_text = docs_by_id[ref_id].claim_text
            else:
                continue
            refs.append(EvidenceRef(ref_id=ref_id, quote=_best_quote(claim_text, source_text, limit)))
        return refs

    def _collect_sources(self, claims, news_by_id, docs_by_id) -> list[SourceRef]:
        used_ids = list(dict.fromkeys(e.ref_id for c in claims for e in c.evidence))
        sources: dict[tuple[SourceKind, str, str], SourceRef] = {}

        def add(ref: SourceRef) -> None:
            sources.setdefault((ref.kind, ref.title, ref.url), ref)

        for ref_id in used_ids:
            if ref_id in news_by_id:
                news = news_by_id[ref_id]
                add(SourceRef(kind=news.source_kind, title=news.title, url=news.url))
                if news.primary_url:
                    add(SourceRef(kind=SourceKind.PRIMARY, title=news.title, url=news.primary_url))
            else:
                doc = docs_by_id[ref_id]
                title = f"{doc.title}, стр. {doc.page}" if doc.page else doc.title
                add(SourceRef(kind=SourceKind.BANK, title=title, url=doc.url))

        ordered = sorted(sources.values(), key=lambda s: _SOURCE_ORDER[s.kind])
        return ordered[: self._config.max_sources]
