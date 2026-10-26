"""Regression tests for moderator callback routing."""
import unittest

from writers_submission.admin_queue import is_queue_page_callback


class WritersAdminRoutingTests(unittest.TestCase):
    def test_pages_only(self):
        for data in ("wadmin:review:0", "wadmin:approved:12", "wadmin:queue:10000", "wadmin:published:2"):
            with self.subTest(data=data):
                self.assertTrue(is_queue_page_callback(data))

    def test_actions_are_not_consumed_by_pagination(self):
        work_id = "550e8400-e29b-41d4-a716-446655440000"
        for data in (
            f"wadmin:date:{work_id}:0",
            f"wadmin:slot:{work_id}:1:10",
            f"wadmin:save:{work_id}:2000000000",
            f"wadmin:priority:{work_id}:1",
            f"wadmin:paymenu:{work_id}",
            f"wadmin:pay:{work_id}:CONFIRMED",
            f"wadmin:unschedule:{work_id}",
            f"wadmin:history:{work_id}",
            "wadmin:queue:-1", "wadmin:queue:10001",
            "wadmin:invalid:0", "wadmin:queue:abc", None,
        ):
            with self.subTest(data=data):
                self.assertFalse(is_queue_page_callback(data))
