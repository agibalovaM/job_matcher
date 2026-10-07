from __future__ import annotations

from .config import Settings
from .db import Store, match_from_row, vacancy_from_row
from .dedupe import fingerprint
from .models import MatchResult, Vacancy
from .scoring import score_vacancy
from .sources_linkedin_email import LinkedInMailbox, parse_alert_email
from .telegram import TelegramClient, format_vacancy_message


MAX_NOTIFY_ATTEMPTS = 5

HH_BROWSER_QUERIES = [
    "IT Project Manager",
    "Delivery Manager",
    "Technical Project Manager",
]


def should_notify(vacancy: Vacancy, result: MatchResult, threshold: int) -> bool:
    if vacancy.source == "linkedin":
        # LinkedIn alerts are already filtered by the subscriptions: send every new job, the score is informational.
        return True
    return result.passed and result.score >= threshold


class JobMatcherApp:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.db_path)
        self.store.init()
        self.telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)

    def process_vacancies(self, vacancies: list[Vacancy], notify: bool = True) -> tuple[int, int]:
        profile = self.store.load_profile()
        threshold = int(self.store.get_state("threshold", str(self.settings.threshold)))
        seen = 0
        sent = 0
        for vacancy in vacancies:
            vacancy_id = self.store.add_vacancy(vacancy, fingerprint(vacancy))
            if vacancy_id is None:
                continue
            seen += 1
            result = score_vacancy(vacancy, profile)
            self.store.save_match(vacancy_id, result)
            if notify and self.store.get_state("paused", "false") != "true" and should_notify(vacancy, result, threshold):
                sent += self.send_vacancy(vacancy_id, vacancy, result)
        return seen, sent

    def send_vacancy(self, vacancy_id: int, vacancy: Vacancy, result: MatchResult) -> bool:
        """A failed send is recorded and retried later by retry_failed_notifications."""
        try:
            self.telegram.send_message(format_vacancy_message(vacancy, result))
        except Exception as exc:
            self.store.mark_notify_failed(vacancy_id)
            print(f"telegram send failed for {vacancy.source_key}: {type(exc).__name__}: {exc}")
            return False
        self.store.mark_notified(vacancy_id)
        return True

    def retry_failed_notifications(self) -> int:
        if self.store.get_state("paused", "false") == "true":
            return 0
        sent = 0
        for row in self.store.failed_notifications(MAX_NOTIFY_ATTEMPTS):
            sent += self.send_vacancy(row["id"], vacancy_from_row(row), match_from_row(row))
        return sent

    def process_linkedin_mailbox(self, mailbox: LinkedInMailbox, notify: bool = True) -> tuple[int, int, int, int]:
        """Returns (emails, fetched, new, sent). An email is marked processed only after its vacancies are."""
        emails = fetched = new = sent = 0
        with mailbox:
            for alert in mailbox.new_messages(self.store.is_email_processed):
                vacancies = parse_alert_email(alert.message)
                seen, notified = self.process_vacancies(vacancies, notify=notify)
                self.store.mark_email_processed(alert.message_id, "linkedin", len(vacancies), "ok" if vacancies else "no_vacancies")
                mailbox.mark_seen(alert.uid)
                emails += 1
                fetched += len(vacancies)
                new += seen
                sent += notified
        return emails, fetched, new, sent
