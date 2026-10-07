import tempfile
import unittest

from job_matcher.app import JobMatcherApp
from job_matcher.config import Settings
from job_matcher.models import Vacancy


class FakeTelegram:
    enabled = True

    def __init__(self):
        self.messages = []

    def send_message(self, text, chat_id=None):
        self.messages.append(text)


class DedupeNotificationTests(unittest.TestCase):
    def test_duplicate_vacancy_not_notified_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(db_path=f"{tmp}/jobs.sqlite", threshold=1, telegram_bot_token="x", telegram_chat_id="1")
            app = JobMatcherApp(settings)
            fake = FakeTelegram()
            app.telegram = fake
            vacancy = Vacancy(
                source="hh.ru",
                source_id="123",
                title="Delivery Manager",
                company="Acme",
                url="https://example.com/job?utm_source=a",
                description="Remote from Example Country. Requirements, risks, SDLC.",
                location="Remote Europe",
                remote=True,
            )
            first = app.process_vacancies([vacancy])
            second = app.process_vacancies([vacancy])
            self.assertEqual(first, (1, 1))
            self.assertEqual(second, (0, 0))
            self.assertEqual(len(fake.messages), 1)


if __name__ == "__main__":
    unittest.main()
