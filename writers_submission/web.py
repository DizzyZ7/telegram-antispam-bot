from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import UUID

from aiohttp import web

from .models import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from .security import (
    SESSION_COOKIE_NAME,
    SessionSigner,
    verify_telegram_init_data,
)
from .uploads import validate_submission_fields

_JSON_BODY_LIMIT = 256 * 1024
_IDEMPOTENCY_KEY_MAX = 128
_UPLOAD_CHUNK_SIZE = 64 * 1024


def _json_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            key: _json_value(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return value


def _json_response(payload: dict[str, Any], *, status: int = 200) -> web.Response:
    return web.json_response(
        _json_value(payload),
        status=status,
        dumps=lambda value: json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )


def _error(code: str, *, status: int, message: str | None = None) -> web.Response:
    payload: dict[str, Any] = {"error": code}
    if message:
        payload["message"] = message
    return _json_response(payload, status=status)


def _public_origin(public_url: str) -> str:
    parsed = urlsplit(public_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Writers Submission public URL is invalid")
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_uuid(raw: str) -> UUID:
    try:
        return UUID(str(raw))
    except (TypeError, ValueError, AttributeError) as exc:
        raise NotFoundError("Resource was not found") from exc


def _idempotency_key(request: web.Request) -> str:
    value = request.headers.get("Idempotency-Key", "").strip()
    if not value:
        raise web.HTTPBadRequest(
            text=json.dumps(
                {"error": "idempotency_key_required"},
                separators=(",", ":"),
            ),
            content_type="application/json",
        )
    if len(value) > _IDEMPOTENCY_KEY_MAX:
        raise web.HTTPBadRequest(
            text=json.dumps(
                {"error": "idempotency_key_invalid"},
                separators=(",", ":"),
            ),
            content_type="application/json",
        )
    return value


async def _bounded_json(request: web.Request) -> dict[str, Any]:
    content_length = request.content_length
    if content_length is not None and content_length > _JSON_BODY_LIMIT:
        raise web.HTTPRequestEntityTooLarge(
            max_size=_JSON_BODY_LIMIT,
            actual_size=content_length,
        )
    try:
        raw = await request.read()
    except web.HTTPRequestEntityTooLarge:
        raise
    if len(raw) > _JSON_BODY_LIMIT:
        raise web.HTTPRequestEntityTooLarge(
            max_size=_JSON_BODY_LIMIT,
            actual_size=len(raw),
        )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "invalid_json"}),
            content_type="application/json",
        ) from exc
    if not isinstance(value, dict):
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "invalid_json"}),
            content_type="application/json",
        )
    return value


def _normalized_fields(payload: dict[str, Any]) -> Any:
    return validate_submission_fields(
        title=payload.get("title"),
        work_type=payload.get("work_type"),
        genre=payload.get("genre"),
        description=payload.get("description"),
        body_text=payload.get("body_text", ""),
        external_url=payload.get("external_url"),
        has_ready_file=bool(payload.get("has_ready_file", False)),
    )


def _file_payload(file: Any) -> dict[str, Any]:
    return {
        "id": getattr(file, "id", None),
        "filename": getattr(file, "safe_filename", None),
        "file_class": getattr(file, "detected_file_class", None),
        "mime": getattr(file, "declared_mime", None),
        "size": getattr(file, "byte_size", None),
        "sha256": getattr(file, "sha256", None),
        "created_at": getattr(file, "created_at", None),
    }


def _bundle_payload(bundle: Any) -> dict[str, Any]:
    revision = getattr(bundle, "revision", None)
    return {
        "id": getattr(bundle, "id", None),
        "status": getattr(bundle, "status", None),
        "version": getattr(bundle, "version", None),
        "created_at": getattr(bundle, "created_at", None),
        "updated_at": getattr(bundle, "updated_at", None),
        "current_draft_revision_id": getattr(
            bundle,
            "current_draft_revision_id",
            None,
        ),
        "current_submitted_revision_id": getattr(
            bundle,
            "current_submitted_revision_id",
            None,
        ),
        "revision": (
            {
                "id": getattr(revision, "id", None),
                "revision_number": getattr(revision, "revision_number", None),
                "state": getattr(revision, "state", None),
                "title": getattr(revision, "title", None),
                "work_type": getattr(revision, "work_type", None),
                "genre": getattr(revision, "genre", None),
                "description": getattr(revision, "description", None),
                "body_text": getattr(revision, "body_text", None),
                "external_url": getattr(revision, "external_url", None),
                "created_at": getattr(revision, "created_at", None),
                "updated_at": getattr(revision, "updated_at", None),
                "sealed_at": getattr(revision, "sealed_at", None),
            }
            if revision is not None
            else None
        ),
    }


class _RateLimiter:
    def __init__(self, *, window_seconds: int, limit: int = 120) -> None:
        self.window_seconds = max(1, int(window_seconds))
        self.limit = max(1, int(limit))
        self._buckets: dict[tuple[int, str], tuple[int, float]] = {}

    def check(self, *, user_id: int, bucket: str, now: float) -> bool:
        key = (int(user_id), str(bucket))
        count, started = self._buckets.get(key, (0, float(now)))
        if float(now) - started >= self.window_seconds:
            count = 0
            started = float(now)
        count += 1
        self._buckets[key] = (count, started)

        if len(self._buckets) > 4096:
            cutoff = float(now) - self.window_seconds
            self._buckets = {
                item_key: item
                for item_key, item in self._buckets.items()
                if item[1] >= cutoff
            }
        return count <= self.limit


class WritersWebServer:
    def __init__(self, app: web.Application, *, host: str, port: int) -> None:
        self.app = app
        self.host = str(host)
        self.port = int(port)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        if self._runner is not None:
            return
        runner = web.AppRunner(self.app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
        except Exception:
            await runner.cleanup()
            raise
        self._runner = runner
        self._site = site

    async def stop(self) -> None:
        runner = self._runner
        self._runner = None
        self._site = None
        if runner is not None:
            await runner.cleanup()


def create_writers_web_app(
    service: Any,
    file_service: Any,
    config: Any,
    session_signer: SessionSigner,
    *,
    bot_token: str,
    now_fn: Callable[[], float] = time.time,
) -> web.Application:
    public_origin = _public_origin(str(config.public_url))
    rate_limiter = _RateLimiter(
        window_seconds=int(config.rate_limit_window_seconds),
    )

    def apply_security_headers(response: web.StreamResponse) -> None:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://telegram.org; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors https://web.telegram.org https://*.telegram.org"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )

    @web.middleware
    async def security_headers(
        request: web.Request,
        handler: Callable[[web.Request], Any],
    ) -> web.StreamResponse:
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            apply_security_headers(exc)
            raise
        apply_security_headers(response)
        return response

    @web.middleware
    async def domain_errors(
        request: web.Request,
        handler: Callable[[web.Request], Any],
    ) -> web.StreamResponse:
        try:
            return await handler(request)
        except AuthorizationError:
            return _error("unauthorized", status=401)
        except NotFoundError:
            return _error("not_found", status=404)
        except ConflictError:
            return _error("conflict", status=409)
        except ValidationError as exc:
            return _error(
                "validation_error",
                status=422,
                message=str(exc),
            )
        except PermissionError:
            return _error("not_eligible", status=403)
        except web.HTTPRequestEntityTooLarge:
            return _error("payload_too_large", status=413)
        except web.HTTPBadRequest as exc:
            if exc.content_type == "application/json" and exc.text:
                return web.Response(
                    text=exc.text,
                    status=400,
                    content_type="application/json",
                )
            return _error("bad_request", status=400)

    app = web.Application(
        middlewares=[security_headers, domain_errors],
        client_max_size=max(
            _JSON_BODY_LIMIT,
            int(config.max_file_bytes) + 128 * 1024,
        ),
    )

    def require_origin(request: web.Request) -> None:
        if request.headers.get("Origin") != public_origin:
            raise web.HTTPForbidden(
                text=json.dumps({"error": "origin_forbidden"}),
                content_type="application/json",
            )

    def actor_id(request: web.Request) -> int:
        raw = request.cookies.get(SESSION_COOKIE_NAME)
        if not raw:
            raise AuthorizationError("Session is required")
        claims = session_signer.verify(raw, now=int(now_fn()))
        return int(claims.user_id)

    def limited_actor(request: web.Request, bucket: str) -> int:
        user_id = actor_id(request)
        if not rate_limiter.check(
            user_id=user_id,
            bucket=bucket,
            now=float(now_fn()),
        ):
            raise web.HTTPTooManyRequests(
                text=json.dumps({"error": "rate_limited"}),
                content_type="application/json",
            )
        return user_id

    async def mini_app(request: web.Request) -> web.Response:
        static_path = Path(__file__).resolve().parent / "static" / "index.html"
        if static_path.is_file():
            return web.FileResponse(static_path)
        return web.Response(
            text=(
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>Writers Submission</title></head>"
                "<body><main>Writers Submission Mini App</main></body></html>"
            ),
            content_type="text/html",
        )

    async def static_asset(request: web.Request) -> web.StreamResponse:
        name = request.match_info["asset"]
        if name not in {"app.js", "app.css"}:
            raise web.HTTPNotFound()
        path = Path(__file__).resolve().parent / "static" / name
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def create_session(request: web.Request) -> web.Response:
        require_origin(request)
        payload = await _bounded_json(request)
        init_data = payload.get("init_data")
        try:
            identity = verify_telegram_init_data(
                init_data,
                bot_token=bot_token,
                now=int(now_fn()),
                max_age_seconds=int(config.init_data_max_age_seconds),
            )
        except AuthorizationError:
            return _error("telegram_auth_invalid", status=401)

        token = session_signer.issue(
            user_id=identity.user_id,
            now=int(now_fn()),
        )
        response = web.Response(status=204)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            token,
            path="/",
            max_age=int(session_signer.ttl_seconds),
            secure=True,
            httponly=True,
            samesite="Strict",
        )
        return response

    async def delete_session(request: web.Request) -> web.Response:
        require_origin(request)
        # Verify an existing cookie when present; clearing remains deterministic.
        raw = request.cookies.get(SESSION_COOKIE_NAME)
        if raw:
            session_signer.verify(raw, now=int(now_fn()))
        response = web.Response(status=204)
        response.del_cookie(SESSION_COOKIE_NAME, path="/")
        return response

    async def list_submissions(request: web.Request) -> web.Response:
        user_id = limited_actor(request, "read")
        items = await service.list_mine(user_id)
        return _json_response(
            {"items": [_json_value(item) for item in items]},
        )

    async def create_submission(request: web.Request) -> web.Response:
        require_origin(request)
        user_id = limited_actor(request, "create")
        key = _idempotency_key(request)
        payload = await _bounded_json(request)
        result = await service.create(
            author_user_id=user_id,
            fields=_normalized_fields(payload),
            idempotency_key=key,
            now=int(now_fn()),
        )
        return _json_response(
            {"submission": _bundle_payload(result)},
            status=201,
        )

    async def get_submission(request: web.Request) -> web.Response:
        user_id = limited_actor(request, "read")
        submission_id = _parse_uuid(request.match_info["submission_id"])
        result = await service.get_mine(user_id, submission_id)
        files_method = getattr(service, "list_files", None)
        files = []
        if callable(files_method):
            files = await files_method(user_id, submission_id)
        return _json_response(
            {
                "submission": _bundle_payload(result),
                "files": [_file_payload(item) for item in files],
            }
        )

    async def update_submission(request: web.Request) -> web.Response:
        require_origin(request)
        user_id = limited_actor(request, "update")
        submission_id = _parse_uuid(request.match_info["submission_id"])
        payload = await _bounded_json(request)
        try:
            expected_version = int(payload["expected_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "expected_version_required"}),
                content_type="application/json",
            ) from exc

        result = await service.autosave(
            author_user_id=user_id,
            submission_id=submission_id,
            expected_version=expected_version,
            fields=_normalized_fields(payload),
            now=int(now_fn()),
        )
        return _json_response({"submission": _bundle_payload(result)})

    async def submit_submission(request: web.Request) -> web.Response:
        require_origin(request)
        user_id = limited_actor(request, "submit")
        key = _idempotency_key(request)
        submission_id = _parse_uuid(request.match_info["submission_id"])
        payload = await _bounded_json(request)
        try:
            expected_version = int(payload["expected_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": "expected_version_required"}),
                content_type="application/json",
            ) from exc
        result = await service.submit(
            author_user_id=user_id,
            submission_id=submission_id,
            expected_version=expected_version,
            idempotency_key=key,
            now=int(now_fn()),
        )
        return _json_response({"submission": _bundle_payload(result)})

    async def withdraw_submission(request: web.Request) -> web.Response:
        require_origin(request)
        user_id = limited_actor(request, "submit")
        key = _idempotency_key(request)
        submission_id = _parse_uuid(request.match_info["submission_id"])
        # Consume/validate JSON when supplied so malformed bodies never slip through.
        if request.can_read_body:
            await _bounded_json(request)
        result = await service.withdraw(
            author_user_id=user_id,
            submission_id=submission_id,
            idempotency_key=key,
            now=int(now_fn()),
        )
        return _json_response({"submission": _bundle_payload(result)})

    async def create_revision(request: web.Request) -> web.Response:
        require_origin(request)
        user_id = limited_actor(request, "create")
        key = _idempotency_key(request)
        submission_id = _parse_uuid(request.match_info["submission_id"])
        if request.can_read_body:
            await _bounded_json(request)
        result = await service.create_revision(
            author_user_id=user_id,
            submission_id=submission_id,
            idempotency_key=key,
            now=int(now_fn()),
        )
        return _json_response(
            {"submission": _bundle_payload(result)},
            status=201,
        )

    async def upload_file(request: web.Request) -> web.Response:
        require_origin(request)
        user_id = limited_actor(request, "upload")
        submission_id = _parse_uuid(request.match_info["submission_id"])

        temp_path: Path | None = None
        try:
            reader = await request.multipart()
            part = await reader.next()
            if part is None or part.name != "file" or not part.filename:
                raise ValidationError("multipart field 'file' is required")

            declared_mime = (
                part.headers.get("Content-Type", "application/octet-stream")
                .split(";", 1)[0]
                .strip()
            )
            with tempfile.NamedTemporaryFile(
                prefix="writers-upload-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                total = 0
                while True:
                    chunk = await part.read_chunk(size=_UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > int(config.max_file_bytes):
                        raise web.HTTPRequestEntityTooLarge(
                            max_size=int(config.max_file_bytes),
                            actual_size=total,
                        )
                    handle.write(chunk)

            result = await file_service.attach_from_temp(
                author_user_id=user_id,
                submission_id=submission_id,
                temp_path=temp_path,
                original_filename=str(part.filename),
                declared_mime=declared_mime,
                now=int(now_fn()),
            )
            return _json_response(
                {
                    "id": result.id,
                    "filename": result.safe_filename,
                    "file_class": result.detected_file_class,
                    "mime": result.declared_mime,
                    "size": result.byte_size,
                    "sha256": result.sha256,
                    "created_at": result.created_at,
                },
                status=201,
            )
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    async def delete_file(request: web.Request) -> web.Response:
        require_origin(request)
        user_id = limited_actor(request, "upload")
        submission_id = _parse_uuid(request.match_info["submission_id"])
        file_id = _parse_uuid(request.match_info["file_id"])
        await file_service.delete_attachment(
            author_user_id=user_id,
            submission_id=submission_id,
            file_id=file_id,
            now=int(now_fn()),
        )
        return web.Response(status=204)

    async def history(request: web.Request) -> web.Response:
        user_id = limited_actor(request, "read")
        submission_id = _parse_uuid(request.match_info["submission_id"])
        history_method = getattr(service, "history", None)
        if not callable(history_method):
            return _json_response({"items": []})
        items = await history_method(user_id, submission_id)
        return _json_response({"items": items})

    app.router.add_get("/writers/", mini_app)
    app.router.add_get("/writers/{asset:app\\.(?:js|css)}", static_asset)
    app.router.add_post("/api/writers/session", create_session)
    app.router.add_delete("/api/writers/session", delete_session)
    app.router.add_get("/api/writers/submissions", list_submissions)
    app.router.add_post("/api/writers/submissions", create_submission)
    app.router.add_get(
        "/api/writers/submissions/{submission_id}",
        get_submission,
    )
    app.router.add_patch(
        "/api/writers/submissions/{submission_id}",
        update_submission,
    )
    app.router.add_post(
        "/api/writers/submissions/{submission_id}/submit",
        submit_submission,
    )
    app.router.add_post(
        "/api/writers/submissions/{submission_id}/withdraw",
        withdraw_submission,
    )
    app.router.add_post(
        "/api/writers/submissions/{submission_id}/revisions",
        create_revision,
    )
    app.router.add_post(
        "/api/writers/submissions/{submission_id}/files",
        upload_file,
    )
    app.router.add_delete(
        "/api/writers/submissions/{submission_id}/files/{file_id}",
        delete_file,
    )
    app.router.add_get(
        "/api/writers/submissions/{submission_id}/history",
        history,
    )

    return app
