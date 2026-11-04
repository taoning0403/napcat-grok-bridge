import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from . import __version__
from .config import Settings
from .grok import GrokClient, GrokDispatcher, validate_grok_url
from .imagehost import ImageHost, ImageHostError, ImageHostUnavailable
from .media import validate_image_url
from .models import (
    GrokConfigRequest,
    GrokWebhook,
    ImageMessageSegment,
    ImageUploadRequest,
    PrivateMessageRequest,
)
from .onebot import MAX_IMAGES, EventProcessor, OneBotClient, OneBotUnavailable, extract_text
from .storage import Store

logger = logging.getLogger("napcat_grok_bridge")


class APIError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


def normalize_private_message(
    payload: PrivateMessageRequest, maximum_length: int
) -> tuple[str | list[dict[str, Any]], str]:
    if isinstance(payload.message, str):
        message = payload.message.strip()
        if not message or len(message) > maximum_length:
            raise ValueError(f"message must be 1-{maximum_length} characters")
        return message, message
    if not payload.message or len(payload.message) > MAX_IMAGES * 2 + 1:
        raise ValueError(f"message must contain 1-{MAX_IMAGES * 2 + 1} segments")
    segments: list[dict[str, Any]] = []
    text_parts: list[str] = []
    image_count = 0
    for segment in payload.message:
        if isinstance(segment, ImageMessageSegment):
            image_count += 1
            if image_count > MAX_IMAGES:
                raise ValueError(f"message cannot contain more than {MAX_IMAGES} images")
            segments.append({"type": "image", "data": {"file": validate_image_url(segment.data.file)}})
        else:
            text_parts.append(segment.data.text)
            if segment.data.text:
                segments.append({"type": "text", "data": {"text": segment.data.text}})
    if len("".join(text_parts)) > maximum_length:
        raise ValueError(f"message text must be at most {maximum_length} characters")
    if not "".join(text_parts).strip() and not image_count:
        raise ValueError("message must contain text or an image")
    return segments, extract_text({"message": segments})


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
    image_host: ImageHost | None = None,
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
    image_host = image_host or ImageHost(settings)
    dispatcher = GrokDispatcher(settings, store, grok_client)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.initialize()
        if initial_config:
            validate_grok_url(initial_config.url, settings.grok_allowed_hosts)
        app.state.store = store
        app.state.dispatcher = dispatcher
        app.state.image_host = image_host
        async with onebot, grok_client, image_host:
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

    @app.post("/grok/images")
    async def upload_image(request: Request):
        if not settings.enable_image_host:
            raise APIError(404, "image host is disabled")
        if not authorized(request, settings.bridge_token):
            raise APIError(403, "unauthorized")
        maximum_request_size = settings.image_host_max_bytes * 4 // 3 + 65536
        try:
            payload = ImageUploadRequest.model_validate(await read_json(request, maximum_request_size))
        except ValidationError as error:
            raise APIError(400, "invalid image upload payload") from error
        try:
            image = await image_host.upload(payload.data)
        except ImageHostError as error:
            raise APIError(400, str(error)) from error
        except ImageHostUnavailable as error:
            raise APIError(502, str(error)) from error
        return {
            "status": "ok",
            "url": f"{settings.image_host_public_base_url}/images/{image.path}",
            "path": image.path,
            "media_type": image.media_type,
            "size": image.size,
        }

    @app.get("/images/{path:path}")
    async def download_image(path: str):
        if not settings.enable_image_host:
            raise APIError(404, "image host is disabled")
        try:
            image = await image_host.download(path)
        except ImageHostUnavailable as error:
            raise APIError(502, str(error)) from error
        if image is None:
            raise APIError(404, "image not found")
        return Response(
            content=image.content,
            media_type=image.media_type,
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )

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
            payload = PrivateMessageRequest.model_validate(await read_json(request, 32768))
        except ValidationError as error:
            raise APIError(400, "invalid private message payload") from error
        if payload.user_id != settings.allowed_peer_id:
            raise APIError(403, "user_id is not allowed")
        try:
            message, pending_text = normalize_private_message(payload, settings.max_message_length)
        except ValueError as error:
            raise APIError(400, str(error)) from error
        store.mark_pending(payload.user_id, pending_text)
        try:
            reply = await upstream_post(
                "/send_private_msg",
                {
                    "user_id": payload.user_id,
                    "message": message,
                    "auto_escape": isinstance(message, str),
                },
            )
        except APIError:
            store.discard_pending(payload.user_id, pending_text)
            raise
        if isinstance(reply.payload, dict) and reply.payload.get("retcode") == 0:
            store.record_outbound(reply.payload.get("data", {}).get("message_id"))
        return JSONResponse(status_code=reply.status, content=reply.payload)

    return app


app = create_app()
