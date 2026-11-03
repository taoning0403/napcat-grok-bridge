import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from . import __version__
from .config import Settings
from .grok import GrokClient, GrokDispatcher, validate_grok_url
from .models import GrokConfigRequest, GrokWebhook, PrivateMessageRequest
from .onebot import EventProcessor, OneBotClient, OneBotUnavailable
from .storage import Store

logger = logging.getLogger("napcat_grok_bridge")


class APIError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


async def read_json(request: Request, maximum: int) -> Any:
    body = await request.body()
    if not body or len(body) > maximum:
        raise APIError(400, "invalid request size")
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise APIError(400, "invalid json") from error


def authorized(request: Request, token: str) -> bool:
    return hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {token}")


def create_app(
    settings: Settings | None = None,
    onebot: Any = None,
    grok_client: GrokClient | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    initial_config = (
        GrokWebhook(settings.grok_webhook_url, settings.grok_webhook_key)
        if settings.grok_webhook_url
        else None
    )
    store = Store(settings.data_dir, settings.retention_days, initial_config)
    processor = EventProcessor(settings, store)
    onebot = onebot or OneBotClient(settings)
    grok_client = grok_client or GrokClient(settings)
    dispatcher = GrokDispatcher(settings, store, grok_client)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.initialize()
        if initial_config:
            validate_grok_url(initial_config.url, settings.grok_allowed_hosts)
        app.state.store = store
        app.state.dispatcher = dispatcher
        async with onebot, grok_client:
            dispatcher.start()
            try:
                yield
            finally:
                await dispatcher.stop()

    app = FastAPI(
        title="NapCat Grok Bridge",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_api_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_api_docs else None,
    )

    @app.middleware("http")
    async def safe_access_log(request: Request, call_next: Any):
        response = await call_next(request)
        logger.info("%s %s -> %s", request.method, request.url.path, response.status_code)
        return response

    @app.exception_handler(APIError)
    async def api_error_handler(_: Request, error: APIError):
        return JSONResponse(status_code=error.status, content={"status": "failed", "message": error.message})

    @app.get("/")
    async def index():
        return {"service": "napcat-grok-bridge", "version": __version__}

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.get("/bridge/status")
    async def bridge_status(request: Request):
        if not authorized(request, settings.bridge_token):
            raise APIError(403, "unauthorized")
        config = store.grok_config()
        result = {
            "status": "ok",
            "allowed_peer_id": settings.allowed_peer_id,
            "bot_user_id": settings.bot_user_id,
            "grok_configured": config is not None,
            **store.status(),
        }
        if config:
            result["grok_host"] = urlsplit(config.url).hostname
        return result

    @app.post("/grok/config")
    async def configure_grok(request: Request):
        if not authorized(request, settings.bridge_token):
            raise APIError(403, "unauthorized")
        try:
            payload = GrokConfigRequest.model_validate(await read_json(request, 8192))
            host = validate_grok_url(payload.url, settings.grok_allowed_hosts)
        except (ValidationError, ValueError) as error:
            raise APIError(400, str(error)) from error
        store.save_grok_config(GrokWebhook(payload.url, payload.key))
        dispatcher.notify()
        return {"status": "ok", "grok_host": host}

    @app.post("/napcat/event")
    async def napcat_event(request: Request):
        header_valid = authorized(request, settings.event_token)
        query_token = request.query_params.get("access_token", "")
        query_valid = hmac.compare_digest(query_token, settings.event_token)
        if not header_valid and not query_valid:
            raise APIError(403, "unauthorized")
        payload = await read_json(request, 65536)
        if not isinstance(payload, dict):
            raise APIError(400, "invalid event")
        result = processor.process(payload)
        if result.get("accepted"):
            dispatcher.notify()
        return {"status": "ok", **result}

    async def upstream_post(path: str, payload: dict[str, Any]):
        try:
            return await onebot.post(path, payload)
        except OneBotUnavailable as error:
            raise APIError(502, str(error)) from error

    @app.post("/get_login_info")
    async def get_login_info(request: Request):
        if not authorized(request, settings.bridge_token):
            raise APIError(403, "unauthorized")
        await read_json(request, 16384)
        reply = await upstream_post("/get_login_info", {})
        return JSONResponse(status_code=reply.status, content=reply.payload)

    @app.post("/send_private_msg")
    async def send_private_message(request: Request):
        if not authorized(request, settings.bridge_token):
            raise APIError(403, "unauthorized")
        try:
            payload = PrivateMessageRequest.model_validate(await read_json(request, 16384))
        except ValidationError as error:
            raise APIError(400, "invalid private message payload") from error
        message = payload.message.strip()
        if not message or len(message) > settings.max_message_length:
            raise APIError(400, f"message must be 1-{settings.max_message_length} characters")
        if payload.user_id != settings.allowed_peer_id:
            raise APIError(403, "user_id is not allowed")
        store.mark_pending(payload.user_id, message)
        try:
            reply = await upstream_post(
                "/send_private_msg",
                {"user_id": payload.user_id, "message": message, "auto_escape": True},
            )
        except APIError:
            store.discard_pending(payload.user_id, message)
            raise
        if isinstance(reply.payload, dict) and reply.payload.get("retcode") == 0:
            store.record_outbound(reply.payload.get("data", {}).get("message_id"))
        return JSONResponse(status_code=reply.status, content=reply.payload)

    return app


app = create_app()
