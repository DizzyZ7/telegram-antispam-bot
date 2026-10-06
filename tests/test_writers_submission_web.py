from __future__ import annotations

import hashlib
import hmac
import json
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode
from uuid import uuid4

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from writers_submission.models import ConflictError, NotFoundError, ValidationError
from writers_submission.security import SESSION_COOKIE_NAME, SessionSigner
from writers_submission.web import create_writers_web_app


BOT_TOKEN = "123456:test-token"
PUBLIC_ORIGIN = "https://example.test"
NOW = 10_000


def valid_init_data(*, user_id: int = 77, auth_date: int = NOW - 10) -> str:
    user = json.dumps(
        {
            "id": user_id,
            "first_name": "Author",
            "username": "author",
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )
    values = {
        "auth_date": str(auth_date),
        "query_id": "AAEAAAE",
        "user": user,
    }
    data_check_string = "\n".join(
        f"{key}={values[key]}" for key in sorted(values)
    )
    secret = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()
    values["hash"] = hmac.new(
        secret,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(values)


def bundle(*, author_user_id: int = 77, version: int = 1):
    submission_id = uuid4()
    revision_id = uuid4()
    return SimpleNamespace(
        id=submission_id,
        writers_chat_id=-1002619489118,
        author_user_id=author_user_id,
        status=SimpleNamespace(value="DRAFT"),
        current_draft_revision_id=revision_id,
        current_submitted_revision_id=None,
        claimed_by_user_id=None,
        claimed_at=None,
        created_at=100,
        updated_at=100,
        version=version,
        revision=SimpleNamespace(
            id=revision_id,
            submission_id=submission_id,
            revision_number=1,
            state=SimpleNamespace(value="DRAFT"),
            title="Работа",
            work_type="Рассказ",
            genre="Фантастика",
            description="Описание",
            body_text="Текст",
            external_url=None,
            created_at=100,
            updated_at=100,
            sealed_at=None,
        ),
    )


class WritersSubmissionWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.item = bundle()
        self.service = SimpleNamespace(
            list_mine=AsyncMock(return_value=[]),
            create=AsyncMock(return_value=self.item),
            get_mine=AsyncMock(return_value=self.item),
            autosave=AsyncMock(return_value=self.item),
            submit=AsyncMock(return_value=self.item),
            withdraw=AsyncMock(return_value=self.item),
            create_revision=AsyncMock(return_value=self.item),
            history=AsyncMock(return_value=[]),
        )
        self.uploaded_file = SimpleNamespace(
            id=uuid4(),
            submission_id=self.item.id,
            revision_id=self.item.revision.id,
            safe_filename="story.txt",
            declared_mime="text/plain",
            detected_file_class="txt",
            byte_size=5,
            sha256="a" * 64,
            telegram_file_id="tg-file",
            telegram_file_unique_id="tg-unique",
            storage_chat_id=-100222,
            storage_message_id=55,
            created_at=100,
        )
        self.service.list_files = AsyncMock(return_value=[self.uploaded_file])
        self.file_service = SimpleNamespace(
            attach_from_temp=AsyncMock(return_value=self.uploaded_file),
            delete_attachment=AsyncMock(),
        )
        self.config = SimpleNamespace(
            public_url="https://example.test/writers/",
            init_data_max_age_seconds=900,
            max_file_bytes=128,
            max_files=3,
            rate_limit_window_seconds=60,
        )
        self.signer = SessionSigner(BOT_TOKEN, ttl_seconds=43_200)
        app = create_writers_web_app(
            self.service,
            self.file_service,
            self.config,
            self.signer,
            bot_token=BOT_TOKEN,
            now_fn=lambda: NOW,
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.cookie = (
            f"{SESSION_COOKIE_NAME}="
            f"{self.signer.issue(user_id=77, now=NOW - 100)}"
        )

    async def asyncTearDown(self):
        await self.client.close()

    def auth_headers(self, *, origin: str | None = PUBLIC_ORIGIN):
        headers = {"Cookie": self.cookie}
        if origin is not None:
            headers["Origin"] = origin
        return headers

    async def test_session_bootstrap_sets_strict_secure_httponly_cookie(self):
        response = await self.client.post(
            "/api/writers/session",
            json={"init_data": valid_init_data()},
            headers={"Origin": PUBLIC_ORIGIN},
        )

        self.assertEqual(response.status, 204)
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn(f"{SESSION_COOKIE_NAME}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Strict", cookie)

    async def test_invalid_and_expired_init_data_are_denied(self):
        invalid = await self.client.post(
            "/api/writers/session",
            json={"init_data": "user=x&hash=bad"},
            headers={"Origin": PUBLIC_ORIGIN},
        )
        expired = await self.client.post(
            "/api/writers/session",
            json={"init_data": valid_init_data(auth_date=NOW - 901)},
            headers={"Origin": PUBLIC_ORIGIN},
        )

        self.assertEqual(invalid.status, 401)
        self.assertEqual(expired.status, 401)
        self.assertEqual((await invalid.json())["error"], "telegram_auth_invalid")
        self.assertEqual((await expired.json())["error"], "telegram_auth_invalid")

    async def test_unauthenticated_api_is_denied_and_logout_clears_cookie(self):
        response = await self.client.get("/api/writers/submissions")
        self.assertEqual(response.status, 401)

        logout = await self.client.delete(
            "/api/writers/session",
            headers=self.auth_headers(),
        )
        self.assertEqual(logout.status, 204)
        cookie = logout.headers.get("Set-Cookie", "")
        self.assertIn(SESSION_COOKIE_NAME, cookie)
        self.assertTrue("Max-Age=0" in cookie or "expires=" in cookie.casefold())

    async def test_security_headers_exist_without_permissive_cors(self):
        response = await self.client.get("/writers/")

        self.assertEqual(response.status, 200)
        self.assertIn("Content-Security-Policy", response.headers)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("Permissions-Policy", response.headers)
        self.assertNotEqual(response.headers.get("Access-Control-Allow-Origin"), "*")

    async def test_http_exception_paths_do_not_emit_aiohttp_return_warning(self):
        payload = {
            "title": "Работа",
            "work_type": "Рассказ",
            "genre": "Фантастика",
            "description": "Описание",
            "body_text": "Текст",
            "external_url": None,
        }
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            response = await self.client.post(
                "/api/writers/submissions",
                json=payload,
                headers={
                    "Cookie": self.cookie,
                    "Idempotency-Key": "no-origin-warning",
                },
            )
            await response.read()

        self.assertEqual(response.status, 403)
        self.assertFalse(
            any(
                "returning HTTPException object is deprecated" in str(item.message)
                for item in caught
            ),
            [str(item.message) for item in caught],
        )


    async def test_state_changing_cookie_requests_require_same_origin(self):
        payload = {
            "title": "Работа",
            "work_type": "Рассказ",
            "genre": "Фантастика",
            "description": "Описание",
            "body_text": "Текст",
            "external_url": None,
        }
        missing = await self.client.post(
            "/api/writers/submissions",
            json=payload,
            headers={
                "Cookie": self.cookie,
                "Idempotency-Key": "create-1",
            },
        )
        wrong = await self.client.post(
            "/api/writers/submissions",
            json=payload,
            headers={
                **self.auth_headers(origin="https://evil.example"),
                "Idempotency-Key": "create-2",
            },
        )

        self.assertEqual(missing.status, 403)
        self.assertEqual(wrong.status, 403)
        self.service.create.assert_not_awaited()

    async def test_actor_identity_comes_only_from_signed_session(self):
        payload = {
            "author_user_id": 999999,
            "title": "Работа",
            "work_type": "Рассказ",
            "genre": "Фантастика",
            "description": "Описание",
            "body_text": "Текст",
            "external_url": None,
        }
        response = await self.client.post(
            "/api/writers/submissions",
            json=payload,
            headers={
                **self.auth_headers(),
                "Idempotency-Key": "create-session-actor",
            },
        )

        self.assertEqual(response.status, 201)
        kwargs = self.service.create.await_args.kwargs
        self.assertEqual(kwargs["author_user_id"], 77)
        self.assertEqual(kwargs["idempotency_key"], "create-session-actor")

    async def test_cross_user_opaque_uuid_maps_to_404(self):
        target = uuid4()
        self.service.get_mine.side_effect = NotFoundError("hidden")

        response = await self.client.get(
            f"/api/writers/submissions/{target}",
            headers=self.auth_headers(origin=None),
        )

        self.assertEqual(response.status, 404)
        payload = await response.json()
        self.assertEqual(payload["error"], "not_found")
        self.service.get_mine.assert_awaited_once_with(77, target)

    async def test_stale_autosave_maps_to_conflict_and_cannot_overwrite(self):
        target = uuid4()
        self.service.autosave.side_effect = ConflictError("stale")
        payload = {
            "expected_version": 1,
            "title": "Старая вкладка",
            "work_type": "Рассказ",
            "genre": "Драма",
            "description": "Описание",
            "body_text": "Текст",
            "external_url": None,
        }

        response = await self.client.patch(
            f"/api/writers/submissions/{target}",
            json=payload,
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status, 409)
        self.assertEqual((await response.json())["error"], "conflict")

    async def test_idempotency_header_is_required_on_durable_action_routes(self):
        target = uuid4()
        create_payload = {
            "title": "Работа",
            "work_type": "Рассказ",
            "genre": "Фантастика",
            "description": "Описание",
            "body_text": "Текст",
            "external_url": None,
        }
        cases = (
            ("post", "/api/writers/submissions", create_payload),
            (
                "post",
                f"/api/writers/submissions/{target}/submit",
                {"expected_version": 1},
            ),
            ("post", f"/api/writers/submissions/{target}/withdraw", {}),
            ("post", f"/api/writers/submissions/{target}/revisions", {}),
        )
        for method, path, payload in cases:
            with self.subTest(path=path):
                response = await self.client.request(
                    method,
                    path,
                    json=payload,
                    headers=self.auth_headers(),
                )
                self.assertEqual(response.status, 400)
                self.assertEqual(
                    (await response.json())["error"],
                    "idempotency_key_required",
                )

    async def test_malformed_json_returns_safe_400_without_traceback(self):
        response = await self.client.post(
            "/api/writers/submissions",
            data="{",
            headers={
                **self.auth_headers(),
                "Content-Type": "application/json",
                "Idempotency-Key": "bad-json",
            },
        )

        body = await response.text()
        self.assertEqual(response.status, 400)
        self.assertNotIn("Traceback", body)
        self.assertEqual(json.loads(body)["error"], "invalid_json")

    async def test_oversized_json_is_rejected_before_service_call(self):
        payload = json.dumps({"title": "x" * 300_000})
        response = await self.client.post(
            "/api/writers/submissions",
            data=payload,
            headers={
                **self.auth_headers(),
                "Content-Type": "application/json",
                "Idempotency-Key": "huge-json",
            },
        )

        self.assertEqual(response.status, 413)
        self.service.create.assert_not_awaited()

    async def test_oversized_multipart_is_rejected_before_staging(self):
        form = FormData()
        form.add_field(
            "file",
            b"x" * 256,
            filename="story.txt",
            content_type="text/plain",
        )
        response = await self.client.post(
            f"/api/writers/submissions/{self.item.id}/files",
            data=form,
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status, 413)
        self.file_service.attach_from_temp.assert_not_awaited()

    async def test_successful_upload_returns_safe_metadata_without_temp_path(self):
        form = FormData()
        form.add_field(
            "file",
            b"hello",
            filename="story.txt",
            content_type="text/plain",
        )

        response = await self.client.post(
            f"/api/writers/submissions/{self.item.id}/files",
            data=form,
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status, 201)
        payload = await response.json()
        self.assertEqual(payload["filename"], "story.txt")
        self.assertEqual(payload["size"], 5)
        self.assertNotIn("path", payload)
        kwargs = self.file_service.attach_from_temp.await_args.kwargs
        self.assertEqual(kwargs["author_user_id"], 77)
        self.assertFalse(Path(kwargs["temp_path"]).exists())

    async def test_temp_upload_is_cleaned_when_file_service_rejects(self):
        captured = {}

        async def reject(**kwargs):
            captured["path"] = Path(kwargs["temp_path"])
            raise ValidationError("bad file")

        self.file_service.attach_from_temp.side_effect = reject
        form = FormData()
        form.add_field(
            "file",
            b"hello",
            filename="bad.txt",
            content_type="text/plain",
        )

        response = await self.client.post(
            f"/api/writers/submissions/{self.item.id}/files",
            data=form,
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status, 422)
        self.assertIn("path", captured)
        self.assertFalse(captured["path"].exists())



    async def test_detail_restores_persisted_files_after_reload(self):
        response = await self.client.get(
            f"/api/writers/submissions/{self.item.id}",
            headers=self.auth_headers(origin=None),
        )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["files"][0]["filename"], "story.txt")
        self.assertNotIn("telegram_file_id", payload["files"][0])
        self.service.list_files.assert_awaited_once_with(77, self.item.id)

    async def test_history_is_scoped_to_session_actor(self):
        self.service.history.return_value = [
            {
                "event_type": "SUBMITTED",
                "created_at": 123,
                "revision_id": str(self.item.revision.id),
            }
        ]
        response = await self.client.get(
            f"/api/writers/submissions/{self.item.id}/history",
            headers=self.auth_headers(origin=None),
        )

        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["items"][0]["event_type"], "SUBMITTED")
        self.service.history.assert_awaited_once_with(77, self.item.id)

    async def test_static_mini_app_contract_is_safe_and_state_aware(self):
        index_response = await self.client.get("/writers/")
        js_response = await self.client.get("/writers/app.js")
        css_response = await self.client.get("/writers/app.css")

        self.assertEqual(index_response.status, 200)
        self.assertEqual(js_response.status, 200)
        self.assertEqual(css_response.status, 200)

        index = await index_response.text()
        script = await js_response.text()
        styles = await css_response.text()

        self.assertIn("https://telegram.org/js/telegram-web-app.js", index)
        self.assertIn("app.js", index)
        self.assertIn("app.css", index)
        self.assertNotIn("BOT_TOKEN", index + script)
        self.assertNotIn("DATABASE_URL", index + script)
        self.assertNotIn("WRITERS_SUBMISSION_MODERATOR_IDS", index + script)

        self.assertIn(".textContent", script)
        self.assertNotIn(".innerHTML =", script)
        self.assertIn("expected_version", script)
        self.assertIn("Idempotency-Key", script)
        self.assertIn("crypto.randomUUID", script)
        self.assertIn("response.status === 409", script)
        self.assertIn("reloadRequired", script)

        self.assertIn("Telegram.WebApp.initData", script)
        self.assertIn("/api/writers/session", script)
        self.assertIn("autosave", script.casefold())
        self.assertIn("timeline", index.casefold())
        self.assertIn("--tg-theme-bg-color", styles)
        self.assertIn("@media", styles)


if __name__ == "__main__":
    unittest.main()
