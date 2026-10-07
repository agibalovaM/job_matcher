from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from .models import CandidateProfile, MatchResult, Vacancy


SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  data TEXT NOT NULL,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vacancies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  title TEXT NOT NULL,
  company TEXT,
  url TEXT,
  description TEXT,
  published_at TEXT,
  updated_at TEXT,
  location TEXT,
  remote INTEGER DEFAULT 0,
  apply_available INTEGER,
  fingerprint TEXT NOT NULL,
  raw TEXT,
  first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source, source_id),
  UNIQUE(fingerprint)
);

CREATE TABLE IF NOT EXISTS matches (
  vacancy_id INTEGER PRIMARY KEY,
  score INTEGER NOT NULL,
  recommendation TEXT NOT NULL,
  reasons TEXT NOT NULL,
  gaps TEXT NOT NULL,
  rejects TEXT NOT NULL,
  criteria TEXT NOT NULL,
  matched_at TEXT NOT NULL,
  notified_at TEXT,
  notify_attempts INTEGER NOT NULL DEFAULT 0,
  notify_failed_at TEXT,
  FOREIGN KEY(vacancy_id) REFERENCES vacancies(id)
);

CREATE TABLE IF NOT EXISTS processed_emails (
  message_id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  vacancies_found INTEGER NOT NULL,
  status TEXT NOT NULL,
  processed_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def vacancy_from_row(row: sqlite3.Row) -> Vacancy:
    return Vacancy(
        source=row["source"],
        source_id=row["source_id"],
        title=row["title"],
        company=row["company"] or "",
        url=row["url"] or "",
        description=row["description"] or "",
        published_at=row["published_at"],
        updated_at=row["updated_at"],
        location=row["location"] or "",
        remote=bool(row["remote"]),
        apply_available=None if row["apply_available"] is None else bool(row["apply_available"]),
        raw=json.loads(row["raw"] or "{}"),
    )


def match_from_row(row: sqlite3.Row) -> MatchResult:
    return MatchResult(
        score=row["score"],
        recommendation=row["recommendation"],
        reasons=json.loads(row["reasons"]),
        gaps=json.loads(row["gaps"]),
        rejects=json.loads(row["rejects"]),
        criteria=json.loads(row["criteria"]),
        matched_at=row["matched_at"],
    )


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

    def init(self) -> None:
        self.conn.executescript(SCHEMA)
        self.migrate()
        self.set_state_default("paused", "false")
        self.conn.commit()

    def migrate(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(matches)")}
        if "notify_attempts" not in columns:
            self.conn.execute("ALTER TABLE matches ADD COLUMN notify_attempts INTEGER NOT NULL DEFAULT 0")
        if "notify_failed_at" not in columns:
            self.conn.execute("ALTER TABLE matches ADD COLUMN notify_failed_at TEXT")

    def set_state_default(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO state(key, value) VALUES(?, ?)", (key, value))

    def get_state(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def save_profile(self, profile: CandidateProfile) -> None:
        self.conn.execute(
            "INSERT INTO profiles(id, data) VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=CURRENT_TIMESTAMP",
            (json.dumps(profile.to_json(), ensure_ascii=False),),
        )
        self.conn.commit()

    def load_profile(self) -> CandidateProfile:
        row = self.conn.execute("SELECT data FROM profiles WHERE id = 1").fetchone()
        if not row:
            profile = CandidateProfile()
            self.save_profile(profile)
            return profile
        return CandidateProfile.from_json(json.loads(row["data"]))

    def add_vacancy(self, vacancy: Vacancy, fingerprint: str) -> Optional[int]:
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO vacancies(
              source, source_id, title, company, url, description, published_at, updated_at,
              location, remote, apply_available, fingerprint, raw
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vacancy.source,
                vacancy.source_id,
                vacancy.title,
                vacancy.company,
                vacancy.url,
                vacancy.description,
                vacancy.published_at,
                vacancy.updated_at,
                vacancy.location,
                int(vacancy.remote),
                None if vacancy.apply_available is None else int(vacancy.apply_available),
                fingerprint,
                json.dumps(vacancy.raw, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid)

    def save_match(self, vacancy_id: int, result: MatchResult) -> None:
        self.conn.execute(
            """
            INSERT INTO matches(vacancy_id, score, recommendation, reasons, gaps, rejects, criteria, matched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(vacancy_id) DO UPDATE SET
              score=excluded.score, recommendation=excluded.recommendation, reasons=excluded.reasons,
              gaps=excluded.gaps, rejects=excluded.rejects, criteria=excluded.criteria, matched_at=excluded.matched_at
            """,
            (
                vacancy_id,
                result.score,
                result.recommendation,
                json.dumps(result.reasons, ensure_ascii=False),
                json.dumps(result.gaps, ensure_ascii=False),
                json.dumps(result.rejects, ensure_ascii=False),
                json.dumps(result.criteria, ensure_ascii=False),
                result.matched_at,
            ),
        )
        self.conn.commit()

    def mark_notified(self, vacancy_id: int) -> None:
        self.conn.execute("UPDATE matches SET notified_at = CURRENT_TIMESTAMP WHERE vacancy_id = ?", (vacancy_id,))
        self.conn.commit()

    def is_notified(self, source: str, source_id: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM vacancies v JOIN matches m ON m.vacancy_id = v.id
            WHERE v.source = ? AND v.source_id = ? AND m.notified_at IS NOT NULL
            """,
            (source, source_id),
        ).fetchone()
        return row is not None

    def mark_notify_failed(self, vacancy_id: int) -> None:
        self.conn.execute(
            "UPDATE matches SET notify_attempts = notify_attempts + 1, notify_failed_at = CURRENT_TIMESTAMP WHERE vacancy_id = ?",
            (vacancy_id,),
        )
        self.conn.commit()

    def failed_notifications(self, max_attempts: int) -> list[sqlite3.Row]:
        """Vacancies whose Telegram send was attempted and failed. Baseline/no-notify rows are never attempted."""
        return self.conn.execute(
            """
            SELECT v.*, m.score, m.recommendation, m.reasons, m.gaps, m.rejects, m.criteria, m.matched_at
            FROM vacancies v JOIN matches m ON m.vacancy_id = v.id
            WHERE m.notified_at IS NULL AND m.notify_failed_at IS NOT NULL AND m.notify_attempts < ?
            ORDER BY v.id
            """,
            (max_attempts,),
        ).fetchall()

    def is_email_processed(self, message_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM processed_emails WHERE message_id = ?", (message_id,)).fetchone()
        return row is not None

    def mark_email_processed(self, message_id: str, source: str, vacancies_found: int, status: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO processed_emails(message_id, source, vacancies_found, status) VALUES (?, ?, ?, ?)",
            (message_id, source, vacancies_found, status),
        )
        self.conn.commit()

    def recent(self, limit: int = 5) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT v.*, m.score, m.recommendation, m.reasons, m.gaps, m.rejects, m.criteria, m.notified_at
            FROM vacancies v JOIN matches m ON m.vacancy_id = v.id
            ORDER BY v.first_seen_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()

