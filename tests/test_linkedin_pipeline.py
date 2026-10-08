import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace

from job_matcher.app import MAX_NOTIFY_ATTEMPTS, JobMatcherApp
from job_matcher.config import Settings
from job_matcher.models import CandidateProfile
from job_matcher.scoring import score_vacancy
from job_matcher.sources_linkedin_email import LinkedInMailbox, parse_alert_email
from tests.linkedin_samples import FakeIMAP, alert_email
from tests.test_dedupe_notifications import FakeTelegram

SHARED = ("4100000001", "Project Manager", "Acme Software · Example City, Example Country (Hybrid)")
EMAIL_A = alert_email("<alert-a@test>", "project manager", [SHARED, ("4100000002", "Delivery Manager", "Globex · Example City, Example Country (Remote)")])
EMAIL_B = alert_email("<alert-b@test>", "it project manager", [("4100000003", "IT Project Manager", "Initech · Example City, Example Country (Hybrid)"), SHARED])


class PipelineCase(unittest.TestCase):
    """Uses the production threshold (4): LinkedIn must be sent regardless of it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(db_path=f"{self.tmp.name}/jobs.sqlite", threshold=4, telegram_bot_token="x", telegram_chat_id="1")
        self.app = JobMatcherApp(self.settings)
        self.telegram = FakeTelegram()
        self.app.telegram = self.telegram

    def tearDown(self):
        self.app.store.conn.close()
        self.tmp.cleanup()

    def mailbox(self, imap, **overrides):
        settings = replace(self.settings, linkedin_imap_user="user@example.com", linkedin_imap_password="app-password", **overrides)
        return LinkedInMailbox.from_settings(settings, imap_factory=imap)

    def one(self, title, company_line, job_id="4100000040"):
        return parse_alert_email(alert_email(f"<{job_id}@test>", "pm", [(job_id, title, company_line)]))


class DedupeTests(PipelineCase):
    def test_same_job_in_two_emails_sent_once(self):
        first = self.app.process_vacancies(parse_alert_email(EMAIL_A))
        second = self.app.process_vacancies(parse_alert_email(EMAIL_B))
        self.assertEqual(first, (2, 2))
        self.assertEqual(second, (1, 1))
        self.assertEqual(sum("/jobs/view/4100000001" in m for m in self.telegram.messages), 1)
        self.assertEqual(len(self.telegram.messages), 3)


class LinkedInNoFilteringTests(PipelineCase):
    """LinkedIn subscriptions are pre-filtered on LinkedIn: every new job is sent, the score is informational."""

    def test_low_score_vacancy_is_sent_with_informational_score(self):
        vacancies = self.one("Technical Project Manager", "Globex · Paphos, Cyprus")
        result = score_vacancy(vacancies[0], CandidateProfile())
        self.assertLess(result.score, 4)
        self.assertEqual(self.app.process_vacancies(vacancies), (1, 1))
        self.assertIn(f"Оценка: {result.score} (для информации, на отправку не влияет)", self.telegram.messages[0])

    def test_former_stop_word_titles_are_sent(self):
        titles = ["SAP Project Manager", "Regional Director_EMEA Sales", "Head of Projects", "Infrastructure Project Manager"]
        for n, title in enumerate(titles):
            vacancies = self.one(title, "Acme · Example City, Example Country (Remote)", job_id=f"41000000{50 + n}")
            self.assertEqual(self.app.process_vacancies(vacancies), (1, 1), title)
        self.assertEqual(len(self.telegram.messages), len(titles))

    def test_scoring_rejects_do_not_block_linkedin(self):
        vacancies = self.one("Project Manager (English C2)", "Acme · United States (Remote)")
        self.assertFalse(score_vacancy(vacancies[0], CandidateProfile()).passed)
        self.assertEqual(self.app.process_vacancies(vacancies), (1, 1))

    def test_other_sources_keep_threshold(self):
        vacancy = self.one("Scrum Master", "Acme · Paphos, Cyprus")[0]
        vacancy.source = "hh-browser"
        vacancy.description = "Office in Paphos."
        self.assertLess(score_vacancy(vacancy, CandidateProfile()).score, 4)
        self.assertEqual(self.app.process_vacancies([vacancy]), (1, 0))
        self.assertEqual(self.telegram.messages, [])

    def test_score_line_only_for_linkedin(self):
        vacancy = self.one("Delivery Manager", "Acme · Example City, Example Country (Remote)")[0]
        vacancy.source = "hh-browser"
        vacancy.description = "Remote from Example Country. Requirements, risks, SDLC."
        self.assertEqual(self.app.process_vacancies([vacancy]), (1, 1))
        self.assertNotIn("Оценка:", self.telegram.messages[0])


class EndToEndTests(PipelineCase):
    def test_mailbox_to_sqlite_to_telegram_and_rerun_has_no_duplicates(self):
        imap = FakeIMAP([EMAIL_A, EMAIL_B])
        result = self.app.process_linkedin_mailbox(self.mailbox(imap))
        self.assertEqual(result, (2, 4, 3, 3))  # emails, fetched, new, sent
        self.assertEqual(len(self.telegram.messages), 3)
        self.assertIn("Источник: LinkedIn · подписка «project manager»", self.telegram.messages[0])
        self.assertIn("Example City, Example Country · Hybrid", self.telegram.messages[0])
        self.assertIn("https://www.linkedin.com/jobs/view/4100000001\n", self.telegram.messages[0])
        count = self.app.store.conn.execute("SELECT COUNT(*) FROM vacancies WHERE source='linkedin'").fetchone()[0]
        self.assertEqual(count, 3)
        self.assertTrue(self.app.store.is_email_processed("<alert-a@test>"))

        rerun = self.app.process_linkedin_mailbox(self.mailbox(imap))
        self.assertEqual(rerun, (0, 0, 0, 0))
        self.assertEqual(len(self.telegram.messages), 3)

    def test_read_only_by_default(self):
        imap = FakeIMAP([EMAIL_A])
        self.app.process_linkedin_mailbox(self.mailbox(imap))
        self.assertTrue(imap.readonly)
        self.assertEqual(imap.stored, [])
        self.assertIn('FROM "jobalerts-noreply@linkedin.com"', imap.searches[0])

    def test_mark_as_read_flag(self):
        imap = FakeIMAP([EMAIL_A])
        self.app.process_linkedin_mailbox(self.mailbox(imap, linkedin_mark_as_read=True))
        self.assertFalse(imap.readonly)
        self.assertEqual(imap.stored, [(b"1", "+FLAGS", "(\\Seen)")])

    def test_baseline_run_saves_without_sending(self):
        imap = FakeIMAP([EMAIL_A])
        self.assertEqual(self.app.process_linkedin_mailbox(self.mailbox(imap), notify=False), (1, 2, 2, 0))
        self.assertEqual(self.telegram.messages, [])
        imap.messages = FakeIMAP([EMAIL_A, EMAIL_B]).messages
        self.assertEqual(self.app.process_linkedin_mailbox(self.mailbox(imap)), (1, 2, 1, 1))


class BrokenTelegram(FakeTelegram):
    def send_message(self, text, chat_id=None):
        raise RuntimeError("telegram down")


class RetryTests(PipelineCase):
    def test_failed_send_is_retried_once_telegram_recovers(self):
        self.app.telegram = BrokenTelegram()
        with redirect_stdout(io.StringIO()):
            result = self.app.process_linkedin_mailbox(self.mailbox(FakeIMAP([EMAIL_A])))
        self.assertEqual(result, (1, 2, 2, 0))
        self.assertTrue(self.app.store.is_email_processed("<alert-a@test>"))

        self.app.telegram = self.telegram
        self.assertEqual(self.app.retry_failed_notifications(), 2)
        self.assertEqual(len(self.telegram.messages), 2)
        self.assertIn("подписка «project manager»", self.telegram.messages[0])
        self.assertEqual(self.app.retry_failed_notifications(), 0)
        self.assertEqual(len(self.telegram.messages), 2)

    def test_baseline_vacancies_are_not_retried(self):
        self.app.process_linkedin_mailbox(self.mailbox(FakeIMAP([EMAIL_A])), notify=False)
        self.assertEqual(self.app.retry_failed_notifications(), 0)
        self.assertEqual(self.telegram.messages, [])

    def test_retry_gives_up_after_max_attempts(self):
        self.app.telegram = BrokenTelegram()
        with redirect_stdout(io.StringIO()):
            self.app.process_vacancies(parse_alert_email(EMAIL_A)[:1])
            for _ in range(MAX_NOTIFY_ATTEMPTS + 2):
                self.app.retry_failed_notifications()
        attempts = self.app.store.conn.execute("SELECT notify_attempts FROM matches").fetchone()[0]
        self.assertEqual(attempts, MAX_NOTIFY_ATTEMPTS)

    def test_paused_skips_retry(self):
        self.app.telegram = BrokenTelegram()
        with redirect_stdout(io.StringIO()):
            self.app.process_vacancies(parse_alert_email(EMAIL_A)[:1])
        self.app.telegram = self.telegram
        self.app.store.set_state("paused", "true")
        self.assertEqual(self.app.retry_failed_notifications(), 0)
        self.app.store.set_state("paused", "false")
        self.assertEqual(self.app.retry_failed_notifications(), 1)


class NotifyTestSelectionTests(PipelineCase):
    def test_skips_already_notified_vacancy(self):
        from job_matcher.cli import select_passing_test_vacancy

        already_sent, fresh = parse_alert_email(EMAIL_A)
        self.app.process_vacancies([already_sent])
        selected = select_passing_test_vacancy(self.app, self.settings, parse_alert_email(EMAIL_A), "linkedin-notify-test")
        self.assertEqual(selected.source_id, f"linkedin-notify-test-{fresh.source_id}")

    def test_low_score_linkedin_vacancy_is_selectable(self):
        from job_matcher.cli import select_passing_test_vacancy

        vacancies = self.one("Technical Project Manager", "Globex · Paphos, Cyprus")
        self.assertIsNotNone(select_passing_test_vacancy(self.app, self.settings, vacancies, "linkedin-notify-test"))


if __name__ == "__main__":
    unittest.main()
