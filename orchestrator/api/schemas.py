"""Схемы HTTP/WebSocket. Единственное место, где используется pydantic; домен о нём не знает."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..application.use_cases import BannersResult, ChatResult
from ..domain.models import CompanyProfile, TraceStep

CompanyId = Field(min_length=1, max_length=64, pattern=r"^[\w\-.]+$")


class BannersRequest(BaseModel):
    company_id: str = CompanyId
    user_id: Optional[str] = Field(default=None, max_length=64)
    role: Optional[str] = Field(default=None, max_length=32)


class ChatRequest(BannersRequest):
    question: str = Field(min_length=1, max_length=2000)


class WsRequest(BaseModel):
    """Единый запрос по WebSocket: `type` выбирает сценарий."""
    type: Literal["banners", "chat"]
    company_id: str = CompanyId
    user_id: Optional[str] = Field(default=None, max_length=64)
    role: Optional[str] = Field(default=None, max_length=32)
    question: Optional[str] = Field(default=None, max_length=2000)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class SourceOut(_Out):
    kind: str
    title: str
    url: str


class BannerOut(_Out):
    banner_id: str
    severity: str
    label: str
    headline: str
    for_you: str
    caveat: bool
    raw_news: bool
    sources: list[SourceOut]
    expires_at: datetime


class ClaimOut(_Out):
    kind: str
    text: str
    source_urls: list[str]


class ChatAnswerOut(_Out):
    answer_id: str
    claims: list[ClaimOut]
    sources: list[SourceOut]
    confidence: float
    caveat: bool
    raw_news: bool
    follow_ups: list[str]


class StepOut(_Out):
    name: str
    duration_ms: float
    ok: bool
    detail: dict


class BannersResponse(BaseModel):
    trace_id: str
    banners: list[BannerOut]
    degraded: list[str] = []
    steps: Optional[list[StepOut]] = None            # только при ?debug=true


class ChatResponse(BaseModel):
    trace_id: str
    answer: ChatAnswerOut
    degraded: list[str] = []
    steps: Optional[list[StepOut]] = None


class ProfileOut(BaseModel):
    activity: str = ""
    company_id: str
    segment_id: str
    features: list[str]
    tags: list[str]


def banners_response(result: BannersResult, debug: bool) -> BannersResponse:
    return BannersResponse(
        trace_id=result.trace_id, banners=[BannerOut.model_validate(b) for b in result.banners],
        degraded=list(result.degraded), steps=_steps(result.steps) if debug else None)


def chat_response(result: ChatResult, debug: bool) -> ChatResponse:
    return ChatResponse(
        trace_id=result.trace_id, answer=ChatAnswerOut.model_validate(result.answer),
        degraded=list(result.degraded), steps=_steps(result.steps) if debug else None)


def profile_out(p: CompanyProfile) -> ProfileOut:
    return ProfileOut(activity=p.activity, company_id=p.company_id, segment_id=p.segment_id, features=list(p.features),
                      tags=sorted(p.tags))


def step_out(s: TraceStep) -> StepOut:
    return StepOut.model_validate(s)


def _steps(steps: tuple[TraceStep, ...]) -> list[StepOut]:
    return [step_out(s) for s in steps]
