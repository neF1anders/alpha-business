"""Сущности ветки «сигнал → саммари → баннеры → уверенность → выдача».

Принцип: у каждого факта одно место хранения.
  * SummaryRequest    — вход от оркестратора, живёт один вызов.
  * SummaryDraft      — что вернула LLM, живёт между S2 и S3, нигде не хранится.
  * SegmentSummary    — ОБЩЕЕ содержимое для всех компаний сегмента (то, что дорого считать). Лежит в SegmentCache.
  * Banner            — ЛИЧНОЕ для компании: строка «для вас», уверенность, срок. Лежит в BannerStore.
  * BannerDTO         — то, что видит фронт; собирается из Banner + SegmentSummary, хранится только как готовая выдача.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- enums
class Mode(str, Enum):
    BANNER = "banner"  # предрасчёт, вопроса нет
    CHAT = "chat"      # ответ в реальном времени


class ClaimType(str, Enum):
    FACT = "fact"
    BANK = "bank"
    MODEL = "model"
    HYPOTHESIS = "hypothesis"


class SourceKind(str, Enum):
    PRIMARY = "primary"
    BANK = "bank"
    MEDIA = "media"


class Severity(str, Enum):
    ATTENTION = "attention"
    WATCH = "watch"
    OPPORTUNITY = "opportunity"


class Decision(str, Enum):
    SHOW_SUMMARY = "show_summary"          # уверенность высокая
    SHOW_WITH_CAVEAT = "show_with_caveat"  # средняя: саммари + «проверьте источник»
    SHOW_RAW_NEWS = "show_raw_news"        # низкая: только заголовок новости и ссылка
    DROP = "drop"                          # не показываем


class BudgetTier(str, Enum):
    FULL = "full"
    LITE = "lite"
    CACHED_ONLY = "cached_only"


# --------------------------------------------------------------------------- 1. вход от оркестратора
class CompanyContext(BaseModel):
    company_id: str
    segment_id: str                                  # ключ сегментного кэша; правило нарезки задаёт ветка профилей
    role: str = "cfo"
    tags: list[str] = Field(default_factory=list)    # машинные теги: макрофакторы, отрасль, география (для матчинга с новостью)
    features: list[str] = Field(default_factory=list)  # человекочитаемые признаки для строки «для вас»: «3 кредитных продукта»


class RankedNewsItem(BaseModel):
    news_id: str
    cluster_id: str
    title: str
    text: str
    source_kind: SourceKind = SourceKind.MEDIA
    url: str
    primary_url: Optional[str] = None                # первоисточник, если новость вторична
    published_at: datetime
    tags: list[str] = Field(default_factory=list)
    rerank_score: float = Field(default=0.0, ge=0, le=1)


class BankMaterialRef(BaseModel):
    doc_id: str
    title: str
    page: Optional[int] = None
    claim_text: str
    url: str


class SummaryRequest(BaseModel):
    """Контракт «оркестратор → наша ветка»."""
    trace_id: str
    mode: Mode
    question: Optional[str] = None                   # 1) вопрос чата, только в режиме chat
    company: CompanyContext                          # 2) тег компании и доп. информация
    news: list[RankedNewsItem] = Field(min_length=1)  # 3) батч новостей от реранкера, по убыванию rerank_score
    bank_materials: list[BankMaterialRef] = Field(default_factory=list)
    budget: BudgetTier = BudgetTier.FULL

    @model_validator(mode="after")
    def _check_mode(self) -> "SummaryRequest":
        if self.mode == Mode.BANNER and self.question:
            raise ValueError("в режиме banner вопроса быть не должно")
        if self.mode == Mode.CHAT and not self.question:
            raise ValueError("в режиме chat нужен вопрос")
        return self


# --------------------------------------------------------------------------- 2. промежуточное
class DraftClaim(BaseModel):
    type: ClaimType
    text: str
    evidence_ids: list[str] = Field(default_factory=list)   # news_id или doc_id из запроса


class SummaryDraft(BaseModel):
    """Формат структурного ответа LLM (S2). После S3 не хранится."""
    headline: str                                    # <= 60 символов
    for_you_template: str                            # может содержать слот {feature}
    for_you_fallback: str                            # общий текст, если подставить нечего
    topic: str                                       # макрофактор: rate, fx, labor, demand ...
    severity: Severity
    claims: list[DraftClaim]
    follow_ups: list[str] = Field(default_factory=list)   # только для chat


class EvidenceRef(BaseModel):
    ref_id: str                                      # news_id или doc_id
    quote: str = ""                                  # <= 200 символов, для «где это сказано»


class Claim(BaseModel):
    type: ClaimType
    text: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    grounding: Optional[float] = Field(default=None, ge=0, le=1)   # считается один раз при сборке (S3b)


class SourceRef(BaseModel):
    kind: SourceKind
    title: str
    url: str


class LeadRef(BaseModel):
    """Минимум о главной новости, нужный для расчёта уверенности по компаниям сегмента."""
    cluster_id: str
    published_at: datetime
    tags: list[str] = Field(default_factory=list)
    rerank_score: float = Field(ge=0, le=1)


class ContentSignals(BaseModel):
    """Уверенность и качество, которые не зависят от компании (считаются один раз на сегмент)."""
    grounding: float = Field(ge=0, le=1)
    source_quality: float = Field(ge=0, le=1)
    claim_mix: float = Field(ge=0, le=1)
    numeric_miss: bool = False
    unsupported_claim: bool = False                  # проверяемое утверждение почти не опирается на слова источника
    # метрика качества саммари SQS (диагностика, в рантайм-решении не участвует)
    faithfulness: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    specificity: float = Field(ge=0, le=1)
    brevity: float = Field(ge=0, le=1)
    structure_ok: bool
    sqs: float = Field(ge=0, le=1)


class ContentKey(BaseModel, frozen=True):
    cluster_id: str
    segment_id: str
    prompt_version: str


class SegmentSummary(BaseModel):
    """Общее содержимое. Один экземпляр на (cluster, segment, prompt_version), на него ссылаются баннеры."""
    key: ContentKey
    model_id: str
    headline: str
    for_you_template: str
    for_you_fallback: str
    topic: str
    severity: Severity
    claims: list[Claim]
    sources: list[SourceRef]
    lead: LeadRef
    signals: ContentSignals
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- 3. уверенность
class ConfidenceComponents(BaseModel):
    relevance: float
    grounding: float
    source_quality: float
    freshness: float
    claim_mix: float


class Confidence(BaseModel):
    score: float = Field(ge=0, le=1)
    decision: Decision
    components: ConfidenceComponents
    reasons: list[str] = Field(default_factory=list)  # идут в трассу, в Banner не копируются


# --------------------------------------------------------------------------- 4. выход
class Banner(BaseModel):
    """Личная часть: что именно эта компания увидит. Содержимое берётся по `key`."""
    banner_id: str
    company_id: str
    key: ContentKey
    topic: str                                       # копия из SegmentSummary: нужна для разнообразия без чтения кэша
    severity: Severity                               # то же, нужна для приоритета
    for_you: str                                     # уже подставленная строка (единственное личное «содержимое»)
    confidence: float = Field(ge=0, le=1)
    decision: Decision
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(default_factory=lambda: utcnow() + timedelta(hours=36))

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        return (now or utcnow()) >= self.expires_at


class BannerDTO(BaseModel):
    """Готовая выдача для BFF. Производная сущность: пересобирается из Banner + SegmentSummary."""
    banner_id: str
    severity: Severity
    label: str                                       # «Внимание · ставка»
    headline: str
    for_you: str
    caveat: bool = False                             # средняя уверенность
    raw_news: bool = False                           # низкая уверенность: headline = заголовок новости, for_you пусто
    sources: list[SourceRef]
    expires_at: datetime


class ChatAnswer(BaseModel):
    answer_id: str
    claims: list[Claim]
    sources: list[SourceRef]
    caveat: bool = False
    raw_news: bool = False
    follow_ups: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
