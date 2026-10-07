from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def split_list(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def env_bool(key: str, default: bool) -> bool:
    value = os.getenv(key, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_path: str = "data/job_matcher.sqlite"
    poll_interval_seconds: int = 1800
    threshold: int = 4
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    linkedin_imap_host: str = "imap.gmail.com"
    linkedin_imap_user: str = ""
    linkedin_imap_password: str = ""
    linkedin_imap_mailbox: str = "INBOX"
    linkedin_senders: tuple[str, ...] = ("jobalerts-noreply@linkedin.com",)
    linkedin_mark_as_read: bool = False
    linkedin_since_days: int = 14
    linkedin_mbox_path: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        load_env()
        return cls(
            db_path=os.getenv("APP_DB_PATH", cls.db_path),
            poll_interval_seconds=int(os.getenv("APP_POLL_INTERVAL_SECONDS", cls.poll_interval_seconds)),
            threshold=int(os.getenv("APP_THRESHOLD", cls.threshold)),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            linkedin_imap_host=os.getenv("LINKEDIN_IMAP_HOST") or cls.linkedin_imap_host,
            linkedin_imap_user=os.getenv("LINKEDIN_IMAP_USER", ""),
            linkedin_imap_password=os.getenv("LINKEDIN_IMAP_PASSWORD", ""),
            linkedin_imap_mailbox=os.getenv("LINKEDIN_IMAP_MAILBOX") or cls.linkedin_imap_mailbox,
            linkedin_senders=split_list(os.getenv("LINKEDIN_SENDER", "")) or cls.linkedin_senders,
            linkedin_mark_as_read=env_bool("LINKEDIN_MARK_AS_READ", cls.linkedin_mark_as_read),
            linkedin_since_days=int(os.getenv("LINKEDIN_SINCE_DAYS") or cls.linkedin_since_days),
            linkedin_mbox_path=os.getenv("LINKEDIN_MBOX_PATH", ""),
        )
