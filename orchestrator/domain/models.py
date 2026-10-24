"""Сущности оркестратора. Без внешних зависимостей: только стандартная библиотека."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Mode(str, Enum):
    BANNER = "banner"
    CHAT = "chat"


class BudgetTier(str, Enum):
    FULL = "full"
    LITE = "lite"
    CACHED_ONLY = "cached_only"


class SourceKind(str, Enum):
    PRIMARY = "primary"
    BANK = "bank"
    MEDIA = "media"


class Severity(str, Enum):
    ATTENTION = "attention"
    WATCH = "watch"
    OPPORTUNITY = "opportunity"


@dataclass(frozen=True)
class CompanyProfile:
    """Профиль клиента: то, что оркестратор знает о компании до обращения к новостям."""
    company_id: str
    segment_id: str                                  # ключ сегментного кэша в ветке саммари
    tags: frozenset[str]                             # `пространство:значение`, см. docs/tags.md
    features: tuple[str, ...] = ()                   # человекочитаемые признаки для строки «для вас»
    role: str = "cfo"

    def with_role(self, role: str) -> "CompanyProfile":
        return CompanyProfile(self.company_id, self.segment_id, self.tags, self.features, role)

    def tags_in(self, *namespaces: str) -> frozenset[str]:
        return frozenset(t for t in self.tags if t.split(":", 1)[0] in namespaces)


@dataclass(frozen=True)
class NewsItem:
    news_id: str
    cluster_id: str
    title: str
    text: str
    url: str
    published_at: datetime
    source_kind: SourceKind = SourceKind.MEDIA
    primary_url: Optional[str] = None
    tags: frozenset[str] = frozenset()
    retrieval_score: float = 0.0                     # оценка retriever, 0..1


@dataclass(frozen=True)
class BankMaterial:
    doc_id: str
    title: str
    claim_text: str
    url: str
    page: Optional[int] = None


@dataclass(frozen=True)
class NewsQuery:
    """Запрос к retriever: баннеры строятся по тегам компании, чат добавляет вопрос."""
    tags: frozenset[str]
    question: Optional[str] = None
    limit: int = 20


@dataclass(frozen=True)
class RetrievalResult:
    news: tuple[NewsItem, ...]
    bank_materials: tuple[BankMaterial, ...] = ()


@dataclass(frozen=True)
class RankedNews:
    item: NewsItem
    score: float                                     # 0..1, итоговая оценка реранкера
    matched_tags: tuple[str, ...] = ()               # какие теги компании совпали: идёт в трассу
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceRef:
    kind: SourceKind
    title: str
    url: str


@dataclass(frozen=True)
class SummaryRequest:
    """Контракт «оркестратор -> ветка саммари». Повторяет `summary_branch.models.SummaryRequest`."""
    trace_id: str
    mode: Mode
    company: CompanyProfile
    news: tuple[RankedNews, ...]
    bank_materials: tuple[BankMaterial, ...] = ()
    question: Optional[str] = None
    budget: BudgetTier = BudgetTier.FULL


@dataclass(frozen=True)
class Banner:
    banner_id: str
    severity: Severity
    label: str
    headline: str
    for_you: str
    sources: tuple[SourceRef, ...]
    expires_at: datetime
    caveat: bool = False
    raw_news: bool = False                           # деградация: только заголовок новости и ссылка


@dataclass(frozen=True)
class Claim:
    kind: str                                        # fact | bank | model | hypothesis
    text: str
    source_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChatAnswer:
    answer_id: str
    claims: tuple[Claim, ...]
    sources: tuple[SourceRef, ...]
    confidence: float
    caveat: bool = False
    raw_news: bool = False
    follow_ups: tuple[str, ...] = ()


@dataclass(frozen=True)
class TraceStep:
    name: str
    duration_ms: float
    ok: bool = True
    detail: dict = field(default_factory=dict)
