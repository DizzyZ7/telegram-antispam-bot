import unittest
from types import SimpleNamespace
from writers_submission.delivery import _author_notification_text


class PublicationNotificationTextTests(unittest.TestCase):
    def test_approval_contains_moscow_schedule(self):
        context = SimpleNamespace(action="APPROVE", title="Тестовая работа", comment=None)
        text = _author_notification_text(context, 1793372400)
        self.assertIn("Работа одобрена", text)
        self.assertIn("МСК", text)
        self.assertIn("Дата публикации", text)

    def test_reschedule_uses_new_date(self):
        context = SimpleNamespace(action="RESCHEDULE", title="Тестовая работа", comment=None)
        text = _author_notification_text(context, 1793458800)
        self.assertIn("Дата публикации изменена", text)
        self.assertIn("МСК", text)

    def test_no_date_on_review_rejection(self):
        context = SimpleNamespace(action="REJECT", title="Тестовая работа", comment=None)
        self.assertNotIn("Дата публикации", _author_notification_text(context))
