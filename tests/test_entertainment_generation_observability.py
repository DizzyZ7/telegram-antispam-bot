from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from entertainment import EntertainmentService
from entertainment.culture import CultureGenerationContext
from entertainment.generation_v3 import GenerationMode, GenerationResult
from entertainment.models import EntertainmentActionRecord, EntertainmentActionType


def obj(**kwargs):
    return SimpleNamespace(**kwargs)


def generation_result(text: str = "новая мемная фраза") -> GenerationResult:
    return GenerationResult(
        text=text,
        engine="v3",
        score=5.1,
        candidate_count=11,
        rejection_counts={"exact_source": 2, "single_source": 1},
    )


def context() -> CultureGenerationContext:
    return CultureGenerationContext(
        source_messages=[
            "автобус сегодня едет к вокзалу и все уже ждут",
            "у вокзала вечером шумно и город еще не спит",
        ],
        context_messages=["где автобус", "ждем его у вокзала"],
        recent_event_count=2,
        historical_event_count=0,
        conversation_run_count=1,
    )


def action(metadata: dict[str, object], *, action_id: int) -> EntertainmentActionRecord:
    return EntertainmentActionRecord(
        id=action_id,
        chat_id=-1001,
        topic_id=10,
        action_type=EntertainmentActionType.REMIXED_PHRASE,
        trigger_message_id=None,
        created_at=4900 + action_id,
        metadata=metadata,
    )


class FakeMessage:
    def __init__(self, *, user_id: int = 7) -> None:
        self.chat = obj(id=-1001, type="supergroup")
        self.from_user = obj(id=user_id, is_bot=False)
        self.message_thread_id = 10
        self.message_id = 900
        self.text = "/fun_generation_status"
        self.reply_to_message = None
        self.replies: list[str] = []

    async def reply(self, text: str, **_kwargs: object) -> None:
        self.replies.append(text)


class FakeBot:
    id = 999

    def __init__(self, *, admin: bool = True) -> None:
        self.admin = admin

    async def get_chat_member(self, *, chat_id: int, user_id: int):
        return obj(status="administrator" if self.admin else "member")


class GenerationObservabilityTests(unittest.IsolatedAsyncioTestCase):
    def service(self, *, admin: bool = True, actions: list[EntertainmentActionRecord] | None = None):
        store = obj(
            recent_actions=AsyncMock(return_value=list(actions or [])),
        )
        service = EntertainmentService(
            obj(bot=FakeBot(admin=admin)),
            store,
            {-1001},
            rng=random.Random(9),
            now_fn=lambda: 5000.0,
        )
        return service, store

    async def test_generation_request_records_one_live_success_not_internal_retries(self):
        service, _store = self.service()
        result = generation_result()

        with (
            patch("entertainment.culture_service.generate_text", return_value=result) as generator,
            patch("entertainment.culture_service.is_novel_generated_text", return_value=True),
            patch("entertainment.culture_service.apply_emoji_style", return_value=(result.text, None)),
        ):
            outcome = service._generate_culture_text(
                context(),
                recent_outputs=[],
                recent_signatures=set(),
                mode=GenerationMode.DIRECT_REPLY,
                trigger_text="где автобус",
            )

        self.assertEqual(outcome.text, result.text)
        self.assertEqual(generator.call_count, 1)
        snapshot = service._generation_metrics.snapshot()
        self.assertEqual(snapshot.attempts, 1)
        self.assertEqual(snapshot.successes, 1)
        self.assertEqual(snapshot.no_output, 0)
        self.assertEqual(snapshot.engine_counts, {"v3": 1})
        self.assertEqual(snapshot.mode_counts, {"direct_reply": 1})
        self.assertEqual(snapshot.rejection_counts, {"exact_source": 2, "single_source": 1})

    async def test_total_generation_failure_records_one_no_output_after_five_internal_attempts(self):
        service, _store = self.service()

        with patch("entertainment.culture_service.generate_text", return_value=None) as generator:
            outcome = service._generate_culture_text(
                context(),
                recent_outputs=[],
                recent_signatures=set(),
                mode=GenerationMode.AUTONOMOUS,
            )

        self.assertIsNone(outcome.text)
        self.assertEqual(generator.call_count, 5)
        snapshot = service._generation_metrics.snapshot()
        self.assertEqual(snapshot.attempts, 1)
        self.assertEqual(snapshot.successes, 0)
        self.assertEqual(snapshot.no_output, 1)
        self.assertEqual(snapshot.engine_counts, {"v3": 1})
        self.assertEqual(snapshot.mode_counts, {"autonomous": 1})

    async def test_admin_generation_status_combines_live_and_persisted_safe_metrics(self):
        actions = [
            action(
                {
                    "generation_engine": "v3",
                    "generation_mode": "autonomous",
                    "generation_candidate_count": 9,
                    "generation_score_bucket": "high",
                    "generation_rejections": {"exact_source": 2},
                    "output": "SECRET OUTPUT MUST NOT LEAK",
                    "trigger_text": "SECRET TRIGGER MUST NOT LEAK",
                },
                action_id=1,
            ),
            action(
                {
                    "generation_engine": "v2",
                    "generation_mode": "direct_reply",
                    "generation_candidate_count": 1,
                    "generation_score_bucket": "none",
                    "generation_rejections": {},
                },
                action_id=2,
            ),
        ]
        service, store = self.service(actions=actions)
        service._generation_metrics.record(
            mode="autonomous",
            engine="v3",
            result=generation_result(),
        )
        service._generation_metrics.record(mode="direct_reply", engine="v3", result=None)
        message = FakeMessage()

        await service.show_generation_status(message)

        store.recent_actions.assert_awaited_once_with(
            -1001,
            10,
            since=5000 - 24 * 60 * 60,
            limit=200,
        )
        rendered = message.replies[-1]
        self.assertIn("v3", rendered)
        self.assertIn("2", rendered)
        self.assertIn("no-output", rendered)
        self.assertIn("24", rendered)
        self.assertIn("ENTERTAINMENT_GENERATION_ENGINE=v2", rendered)
        self.assertNotIn("success rate по сохраненным", rendered)
        self.assertNotIn("SECRET OUTPUT", rendered)
        self.assertNotIn("SECRET TRIGGER", rendered)

    async def test_generation_status_is_admin_only_and_does_not_read_history_for_member(self):
        service, store = self.service(admin=False)
        message = FakeMessage(user_id=77)

        await service.show_generation_status(message)

        store.recent_actions.assert_not_awaited()
        self.assertIn("администрац", message.replies[-1].casefold())


if __name__ == "__main__":
    unittest.main()
