"""Сущности оркестратора. Без внешних зависимостей: только стандартная библиотека."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal, Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Mode(str, Enum):
    BANNER = "banner"
    CHAT = "chat"


class BudgetTier(str, Enum):
    FULL = "full"
    LITE = "lite"
    CACHED_ONLY = "cached_only"


@dataclass(frozen=True)
class ChatTurn:
    """Реплика предыдущего диалога: контекст вопроса, а не источник фактов."""
    role: Literal["user", "assistant"]
    text: str


CHAT_HISTORY_TURNS = 4            # две пары «вопрос — ответ»
CHAT_TURN_MAX_CHARS = 600
_FOLLOW_UP_MAX_WORDS = 4
_CONTENT_WORD = re.compile(r"\w{3,}", re.UNICODE)


def recent_history(history: tuple[ChatTurn, ...]) -> tuple[ChatTurn, ...]:
    return tuple(ChatTurn(t.role, t.text[:CHAT_TURN_MAX_CHARS]) for t in history[-CHAT_HISTORY_TURNS:])


def follow_up_search_text(question: str, history: tuple[ChatTurn, ...]) -> Optional[str]:
    """Короткое уточнение («а с юанем?») само ничего не найдёт: ищем вместе с прошлым вопросом пользователя."""
    if len(_CONTENT_WORD.findall(question)) > _FOLLOW_UP_MAX_WORDS:
        return None
    previous = next((t.text for t in reversed(history) if t.role == "user"), None)
    return f"{previous} {question}" if previous else None


class SourceKind(str, Enum):
    PRIMARY = "primary"
    BANK = "bank"
    MEDIA = "media"


class Role(str, Enum):
    """Должность того, кто смотрит выдачу компании: от неё зависят акценты баннеров и текст «для вас»."""
    OWNER = "owner"
    CEO = "ceo"
    CFO = "cfo"
    TREASURER = "treasurer"
    CHIEF_ACCOUNTANT = "chief_accountant"
    PROCUREMENT = "procurement"
    SALES = "sales"


def audience_id(company_id: str, role: Optional[str]) -> str:
    """Ключ выдачи: баннеры, реакции и стрим делятся по паре компания × роль; без роли — по компании."""
    return f"{company_id}~{role}" if role else company_id


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
    activity: str = ""                             # confirmed source mainokved_name
    audience: str = ""                               # ключ выдачи, см. audience_id; пусто — выдача компании

    @property
    def audience_id(self) -> str:
        return self.audience or self.company_id

    def with_role(self, role: str) -> "CompanyProfile":
        return replace(self, role=role, audience=audience_id(self.company_id, role))

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
    content_kind: str = "full_text"                  # full_text | excerpt | title_only: полнота материала


@dataclass(frozen=True)
class BankMaterial:
    doc_id: str
    title: str
    claim_text: str
    url: str
    page: Optional[int] = None
    news_ids: tuple[str, ...] = ()
    published_at: Optional[date] = None


@dataclass(frozen=True)
class EventContext:
    news_id: str
    company_id: str
    topics: tuple[str, ...]
    event_tags: tuple[str, ...]
    matched_company_tags: tuple[str, ...]
    heuristic_score: float
    bank_materials: tuple[BankMaterial, ...]
    status: str
    trace: dict


@dataclass(frozen=True)
class NewsQuery:
    """Запрос к retriever: баннеры строятся по тегам компании, чат добавляет вопрос."""
    tags: frozenset[str]
    question: Optional[str] = None
    limit: int = 20
    as_of: Optional[datetime] = None
    published_after: Optional[datetime] = None
    company: Optional[CompanyProfile] = None


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
    ref_id: str = ""                                 # news_id или doc_id: связь утверждения чата с источником
    content_kind: str = "full_text"                  # клиенту показывается, что источник — анонс или заголовок


@dataclass(frozen=True)
class CounterpartySector:
    """Контрагенты одной отрасли с одной стороны платёжного потока; без имён и сумм."""
    division: str                                    # двузначный раздел ОКВЭД
    name: str
    share: float                                     # доля суммы потока этой стороны, 0..1
    counterparties: int
    foreign_trade_share: float = 0.0                 # доля суммы с контрагентами, у которых есть ВЭД


@dataclass(frozen=True)
class CompanyDossier:
    """Всё, что данные банка говорят о компании сверх тегов: профиль, платёжный граф, связанные компании.

    Платежи внутри группы и между компаниями с общими физлицами исключены: это переводы «себе».
    """
    okved_code: str = ""
    okved_name: str = ""
    region: str = ""
    city: str = ""
    client_type: str = ""                            # client | prospect | close_client
    products: frozenset[str] = frozenset()           # rko, guarantee, loan, leasing, deposit, payroll, factoring
    trade_countries: tuple[str, ...] = ()
    foreign_trade: bool = False
    revenue: Optional[float] = None
    workers_band: str = ""
    lifecycle: str = ""
    age_band: str = ""
    negative_flag: bool = False
    group_id: str = ""
    suppliers: tuple[CounterpartySector, ...] = ()   # компания платит; по убыванию доли
    buyers: tuple[CounterpartySector, ...] = ()      # платят компании
    top_supplier_share: float = 0.0
    top_buyer_share: float = 0.0
    affiliate_sectors: tuple[str, ...] = ()          # разделы ОКВЭД компаний группы и связанных через общих лиц


@dataclass(frozen=True)
class SummaryRequest:
    """Контракт «оркестратор -> ветка саммари». В режиме banner `news` содержит одну новость (один кластер)."""
    trace_id: str
    mode: Mode
    company: CompanyProfile
    news: tuple[RankedNews, ...]
    bank_materials: tuple[BankMaterial, ...] = ()
    question: Optional[str] = None
    budget: BudgetTier = BudgetTier.FULL
    dossier: Optional[CompanyDossier] = None
    history: tuple[ChatTurn, ...] = ()


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
    cluster_id: str = ""
    placements: tuple[str, ...] = ()                 # страницы действий для плашки: payments, counterparties, new_payment


def raw_banner_id(cluster_id: str) -> str:
    """Id баннера «только новость»: по нему клиент заменяет его готовым баннером того же кластера."""
    return f"raw-{cluster_id}"


@dataclass(frozen=True)
class Evidence:
    ref_id: str
    quote: str = ""


@dataclass(frozen=True)
class Claim:
    kind: str                                        # fact | bank | model | hypothesis
    text: str
    source_urls: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    grounding: Optional[float] = None


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


@dataclass(frozen=True)
class SignalEvent:
    """Событие открытой сессии клиента: новые баннеры или снятие устаревших (по id)."""
    kind: str                                        # banners | invalidate
    banners: tuple[Banner, ...] = ()
    banner_ids: tuple[str, ...] = ()
