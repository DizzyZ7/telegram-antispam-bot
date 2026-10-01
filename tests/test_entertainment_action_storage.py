from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from entertainment.models import EntertainmentActionRecord, EntertainmentActionType
from entertainment.storage.sqlite import SQLiteEntertainmentStorage


class EntertainmentActionStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "actions.db"
        self.storage = SQLiteEntertainmentStorage(self.path)
        await self.storage.initialize()

    async def asyncTearDown(self) -> None:
        await self.storage.close()
        self.temp_dir.cleanup()

    async def test_action_history_is_topic_local_ordered_and_metadata_round_trips(self) -> None:
        first_id = await self.storage.record_action(
            EntertainmentActionRecord(
                id=None,
                chat_id=-1001,
                topic_id=10,
                action_type=EntertainmentActionType.REMIXED_PHRASE,
                trigger_message_id=101,
                created_at=1_000,
                metadata={"phase": "active", "score": 0.72},
            )
        )
        second_id = await self.storage.record_action(
            EntertainmentActionRecord(
                id=None,
                chat_id=-1001,
                topic_id=10,
                action_type=EntertainmentActionType.CONTEXTUAL_REPLY,
                trigger_message_id=102,
                created_at=1_100,
                metadata={"phase": "cooldown", "candidate": "text"},
            )
        )
        await self.storage.record_action(
            EntertainmentActionRecord(
                id=None,
                chat_id=-1001,
                topic_id=20,
                action_type=EntertainmentActionType.MEMORY_CALLBACK,
                trigger_message_id=201,
                created_at=1_200,
                metadata={"isolated": True},
            )
        )

        self.assertGreater(first_id, 0)
        self.assertGreater(second_id, first_id)
        actions = await self.storage.recent_actions(-1001, 10, since=900, limit=20)
        self.assertEqual([action.id for action in actions], [second_id, first_id])
        self.assertEqual(
            [action.action_type for action in actions],
            [
                EntertainmentActionType.CONTEXTUAL_REPLY,
                EntertainmentActionType.REMIXED_PHRASE,
            ],
        )
        self.assertEqual(actions[0].metadata["phase"], "cooldown")
        self.assertEqual(actions[1].metadata["score"], 0.72)

    async def test_action_history_survives_storage_restart(self) -> None:
        await self.storage.record_action(
            EntertainmentActionRecord(
                id=None,
                chat_id=-1002,
                topic_id=77,
                action_type=EntertainmentActionType.REMIXED_PHRASE,
                trigger_message_id=None,
                created_at=2_000,
                metadata={"restart": "safe"},
            )
        )
        await self.storage.close()
        self.storage = SQLiteEntertainmentStorage(self.path)
        await self.storage.initialize()

        actions = await self.storage.recent_actions(-1002, 77, since=1_900)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].created_at, 2_000)
        self.assertEqual(actions[0].metadata, {"restart": "safe"})

    async def test_human_messages_since_last_action_supports_restart_safe_budget(self) -> None:
        await self.storage.add_message(-1003, 5, 1, "before", created_at=2_900)
        await self.storage.add_message(-1003, 5, 2, "after one", created_at=3_010)
        await self.storage.add_message(-1003, 5, 3, "after two", created_at=3_020)
        await self.storage.add_message(-1003, 6, 4, "other topic", created_at=3_030)

        self.assertEqual(await self.storage.human_messages_since(-1003, 5, since=3_000), 2)
        self.assertEqual(await self.storage.human_messages_since(-1003, 5, since=3_020), 1)
        self.assertEqual(await self.storage.human_messages_since(-1003, 6, since=3_000), 1)

    async def test_recent_actions_respects_since_and_limit(self) -> None:
        for created_at in (4_000, 4_100, 4_200):
            await self.storage.record_action(
                EntertainmentActionRecord(
                    id=None,
                    chat_id=-1004,
                    topic_id=1,
                    action_type=EntertainmentActionType.REMIXED_PHRASE,
                    trigger_message_id=None,
                    created_at=created_at,
                    metadata={},
                )
            )
        actions = await self.storage.recent_actions(-1004, 1, since=4_050, limit=1)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].created_at, 4_200)


if __name__ == "__main__":
    unittest.main()
