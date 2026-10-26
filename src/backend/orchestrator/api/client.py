"""HTTP-вход клиента (фронт ассистента): `/api/companies/{company_id}/...`, SSE и формат ошибок `{error, message}`.

Тонкий слой: разбор запроса, вызов use case, сериализация. Будущий BFF забирает этот модуль целиком,
ядро остаётся на `/v1`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Literal, Optional

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from ..application.ports import SignalStream, SignalSubscription
from ..application.use_cases import (
    AnswerChat, BannersCommand, ChatCommand, GetInlineSignal, GetSignals, SignalFeedback,
)
from ..domain.errors import CompanyNotFound, DependencyUnavailable
from ..domain.models import Banner, ChatAnswer, Role, SignalEvent, SourceRef, audience_id

log = logging.getLogger("orchestrator.api.client")

PREFIX = "/api/companies/{company_id}"
HEARTBEAT_S = 15.0
DEMO_ALIAS = "demo-company"                          # id по умолчанию во фронте (src/frontend/src/config.js)
# Роль сотрудника из демо-входа. Query, а не заголовок: EventSource (SSE) заголовки не передаёт.
RoleQuery = Query(default=None, description="должность смотрящего; меняет акценты и кэш выдачи")


@dataclass(frozen=True)
class ClientServices:
    get_signals: GetSignals
    inline: GetInlineSignal
    feedback: SignalFeedback
    answer_chat: AnswerChat
    stream: SignalStream
    demo_company_id: str = ""


class ClientError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# --------------------------------------------------------------------------- схемы
class SourceOut(BaseModel):
    kind: str
    title: str
    url: str
    ref_id: str = ""


class BannerOut(BaseModel):
    banner_id: str
    severity: str
    label: str
    headline: str
    for_you: str
    caveat: bool
    raw_news: bool
    sources: list[SourceOut]
    expires_at: datetime
    cluster_id: str = ""
    placements: list[str] = []
    inline_text: str = ""


class SignalsOut(BaseModel):
    counts: dict[str, int]
    items: list[BannerOut]


class InlineOut(BaseModel):
    item: Optional[BannerOut] = None


class EvidenceOut(BaseModel):
    ref_id: str
    quote: str = ""


class ClaimOut(BaseModel):
    type: str
    text: str
    evidence: list[EvidenceOut]
    grounding: Optional[float] = None


class ChatAnswerOut(BaseModel):
    answer_id: str
    claims: list[ClaimOut]
    sources: list[SourceOut]
    caveat: bool
    raw_news: bool
    follow_ups: list[str]
    confidence: float


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    banner_id: Optional[str] = Field(default=None, max_length=128)


class SuppressIn(BaseModel):
    reason: str = Field(default="not_relevant", max_length=64)


InlinePage = Literal["payments", "counterparties", "new_payment"]


# --------------------------------------------------------------------------- роутер
def register_client_api(app: FastAPI, services: ClientServices) -> None:
    router = APIRouter(prefix=PREFIX, tags=["client"])

    def company(company_id: str) -> str:
        if company_id == DEMO_ALIAS and services.demo_company_id:
            return services.demo_company_id
        if not 1 <= len(company_id) <= 64:
            raise ClientError(422, "invalid_request", "некорректный company_id")
        return company_id

    def audience(company_id: str, role: Optional[Role]) -> str:
        return audience_id(company(company_id), _role(role))

    @router.get("/signals", response_model=SignalsOut)
    async def signals(company_id: str, request: Request, role: Optional[Role] = RoleQuery):
        try:
            result = await services.get_signals.execute(BannersCommand(company(company_id), _role(role)))
        except CompanyNotFound as exc:
            raise ClientError(404, "company_not_found", str(exc)) from exc
        base = _base(request)
        return SignalsOut(counts=result.counts(), items=[banner_out(b, base) for b in result.items])

    @router.get("/signals/inline", response_model=InlineOut)
    async def inline(company_id: str, page: InlinePage, request: Request, role: Optional[Role] = RoleQuery):
        banner = await services.inline.execute(audience(company_id, role), page)
        return InlineOut(item=banner_out(banner, _base(request)) if banner else None)

    @router.get("/signals/stream")
    async def stream(company_id: str, request: Request, role: Optional[Role] = RoleQuery):
        subscription = services.stream.subscribe(audience(company_id, role))
        return StreamingResponse(_sse(subscription, request, _base(request)), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.post("/signals/{banner_id}/seen", status_code=204)
    async def seen(company_id: str, banner_id: str, role: Optional[Role] = RoleQuery):
        await services.feedback.seen(audience(company_id, role), banner_id)
        return Response(status_code=204)

    @router.post("/signals/{banner_id}/suppress", status_code=204)
    async def suppress(company_id: str, banner_id: str, body: Optional[SuppressIn] = None,
                       role: Optional[Role] = RoleQuery):
        await services.feedback.not_relevant(audience(company_id, role), banner_id, (body or SuppressIn()).reason)
        return Response(status_code=204)

    @router.post("/signals/{banner_id}/dismiss", status_code=204)
    async def dismiss(company_id: str, banner_id: str, role: Optional[Role] = RoleQuery):
        await services.feedback.dismiss(audience(company_id, role), banner_id)
        return Response(status_code=204)

    @router.post("/chat", response_model=ChatAnswerOut)
    async def chat(company_id: str, body: ChatIn, request: Request, role: Optional[Role] = RoleQuery):
        cmd = ChatCommand(company(company_id), body.question, _role(role), banner_id=body.banner_id)
        try:
            result = await services.answer_chat.execute(cmd)
        except CompanyNotFound as exc:
            raise ClientError(404, "company_not_found", str(exc)) from exc
        except DependencyUnavailable as exc:
            raise ClientError(503, "unavailable", str(exc)) from exc
        return answer_out(result.answer, _base(request))

    app.include_router(router)
    _register_errors(app)


def _register_errors(app: FastAPI) -> None:
    @app.exception_handler(ClientError)
    async def client_error(_: Request, exc: ClientError):
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status)

    default_validation = app.exception_handlers.get(RequestValidationError)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "invalid_request", "message": "некорректный запрос"}, status_code=422)
        if default_validation is not None:
            return await default_validation(request, exc)
        from fastapi.exception_handlers import request_validation_exception_handler
        return await request_validation_exception_handler(request, exc)


# --------------------------------------------------------------------------- сериализация
def _role(role: Optional[Role]) -> Optional[str]:
    return role.value if role else None


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _url(url: str, base: str) -> str:
    """Материалы банка отдаются ядром (`/v1/materials/...`): фронт на другом origin получает абсолютную ссылку."""
    return f"{base}{url}" if url.startswith("/") else url


def _source(s: SourceRef, base: str) -> SourceOut:
    return SourceOut(kind=s.kind.value, title=s.title, url=_url(s.url, base), ref_id=s.ref_id)


def banner_out(b: Banner, base: str) -> BannerOut:
    return BannerOut(banner_id=b.banner_id, severity=b.severity.value, label=b.label, headline=b.headline,
                     for_you=b.for_you, caveat=b.caveat, raw_news=b.raw_news,
                     sources=[_source(s, base) for s in b.sources], expires_at=b.expires_at, cluster_id=b.cluster_id,
                     placements=list(b.placements), inline_text=b.for_you)


def answer_out(a: ChatAnswer, base: str) -> ChatAnswerOut:
    by_url = {s.url: s.ref_id for s in a.sources if s.ref_id}
    claims = [ClaimOut(type=c.kind, text=c.text, grounding=c.grounding,
                       evidence=[EvidenceOut(ref_id=e.ref_id, quote=e.quote) for e in c.evidence]
                       or [EvidenceOut(ref_id=by_url[u]) for u in c.source_urls if u in by_url])
              for c in a.claims]
    return ChatAnswerOut(answer_id=a.answer_id, claims=claims, sources=[_source(s, base) for s in a.sources],
                         caveat=a.caveat, raw_news=a.raw_news, follow_ups=list(a.follow_ups), confidence=a.confidence)


def sse_frame(event: SignalEvent, base: str) -> str:
    if event.kind == "banners":
        data = [banner_out(b, base).model_dump(mode="json") for b in event.banners]
    else:
        data = list(event.banner_ids)
    return f"event: {event.kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _sse(subscription: SignalSubscription, request: Request, base: str) -> AsyncIterator[str]:
    yield ": connected\n\n"
    try:
        while not await request.is_disconnected():
            try:
                event = await asyncio.wait_for(subscription.next(), timeout=HEARTBEAT_S)
            except asyncio.TimeoutError:
                yield ": ping\n\n"                      # держит соединение через прокси и замечает отключение
                continue
            yield sse_frame(event, base)
    finally:
        subscription.close()
