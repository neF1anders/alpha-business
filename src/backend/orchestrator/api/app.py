"""FastAPI: HTTP и WebSocket вход оркестратора. Здесь только транспорт, логика в application/."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from ..application.ports import ProfileRepository
from ..application.use_cases import AnswerChat, BannersCommand, ChatCommand, GetBanners
from ..domain.errors import CompanyNotFound, DependencyUnavailable
from ..domain.models import TraceStep
from . import schemas
from .client import ClientServices, register_client_api

log = logging.getLogger("orchestrator.api")


@dataclass(frozen=True)
class Container:
    profiles: ProfileRepository
    get_banners: GetBanners
    answer_chat: AnswerChat
    startup: tuple[Callable[[], Awaitable[None]], ...] = ()   # выполняется до приёма запросов
    client: Optional[ClientServices] = None                   # вход фронта `/api/companies/...`


def create_app(container: Container, cors_origins: tuple[str, ...] = ()) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        for hook in container.startup:
            await hook()
        yield

    app = FastAPI(title="Альфа Инсайты: оркестратор", version="0.1.0", lifespan=lifespan,
                  description="Вход и выход системы: баннеры (SLO ~4 с) и чат (SLO ~6 с).")
    if cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(cors_origins), allow_methods=["GET", "POST"],
                           allow_headers=["Content-Type"])
    if container.client is not None:
        register_client_api(app, container.client)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/banners", response_model=schemas.BannersResponse)
    async def banners(body: schemas.BannersRequest, debug: bool = Query(False)):
        """Триггер «сессия началась»: самые релевантные баннеры компании."""
        try:
            result = await container.get_banners.execute(BannersCommand(body.company_id, body.role))
        except CompanyNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return schemas.banners_response(result, debug)

    @app.post("/v1/chat", response_model=schemas.ChatResponse)
    async def chat(body: schemas.ChatRequest, debug: bool = Query(False)):
        try:
            result = await container.answer_chat.execute(ChatCommand(body.company_id, body.question, body.role))
        except CompanyNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except DependencyUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        return schemas.chat_response(result, debug)

    @app.get("/v1/companies/{company_id}/profile", response_model=schemas.ProfileOut)
    async def profile(company_id: str):
        """Какие признаки компании использует система: для трассируемости выводов."""
        found = await container.profiles.get(company_id)
        if found is None:
            raise HTTPException(404, str(CompanyNotFound(company_id)))
        return schemas.profile_out(found)

    @app.websocket("/v1/ws")
    async def ws(socket: WebSocket) -> None:
        """Сессия пользователя: клиент шлёт {"type": "banners"|"chat", ...}, получает progress и итог."""
        await socket.accept()
        try:
            while True:
                raw = await socket.receive_json()
                try:
                    req = schemas.WsRequest.model_validate(raw)
                    if req.type == "chat" and not req.question:
                        raise ValueError("для type=chat нужен question")
                except (ValidationError, ValueError) as exc:
                    await socket.send_json({"type": "error", "code": 422, "message": str(exc)})
                    continue
                await _serve_ws(container, socket, req)
        except WebSocketDisconnect:
            log.info("ws: клиент отключился")

    return app


async def _serve_ws(container: Container, socket: WebSocket, req: schemas.WsRequest) -> None:
    queue: asyncio.Queue[TraceStep] = asyncio.Queue()
    if req.type == "banners":
        work = asyncio.create_task(container.get_banners.execute(BannersCommand(req.company_id, req.role),
                                                                 on_step=queue.put_nowait))
    else:
        cmd = ChatCommand(req.company_id, req.question or "", req.role)
        work = asyncio.create_task(container.answer_chat.execute(cmd, on_step=queue.put_nowait))
    try:
        while not work.done() or not queue.empty():
            getter = asyncio.create_task(queue.get())
            await asyncio.wait({work, getter}, return_when=asyncio.FIRST_COMPLETED)
            if getter.done():
                step = getter.result()
                await socket.send_json({"type": "progress", "step": step.name, "ms": step.duration_ms})
            else:
                getter.cancel()
        result = work.result()
    except CompanyNotFound as exc:
        await socket.send_json({"type": "error", "code": 404, "message": str(exc)})
        return
    except DependencyUnavailable as exc:
        await socket.send_json({"type": "error", "code": 503, "message": str(exc)})
        return
    except Exception:  # noqa: BLE001 - соединение должно пережить сбой одного запроса
        log.exception("ws: необработанная ошибка")
        await socket.send_json({"type": "error", "code": 500, "message": "внутренняя ошибка"})
        return
    if req.type == "banners":
        await socket.send_json({"type": "banners", **schemas.banners_response(result, False).model_dump(mode="json")})
    else:
        await socket.send_json({"type": "answer", **schemas.chat_response(result, False).model_dump(mode="json")})
