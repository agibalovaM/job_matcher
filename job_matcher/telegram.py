from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from .models import MatchResult, Vacancy
from .scoring import BORDERLINE


class TelegramError(RuntimeError):
    pass


@dataclass
class TelegramClient:
    token: str
    chat_id: str

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def api(self, method: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        if not self.token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not configured")
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            raise TelegramError(str(body))
        return body

    def send_message(self, text: str, chat_id: Optional[str] = None) -> None:
        target = chat_id or self.chat_id
        if not target:
            raise TelegramError("TELEGRAM_CHAT_ID is not configured")
        self.api("sendMessage", {"chat_id": target, "text": text, "disable_web_page_preview": False})

    def get_updates(self, offset: Optional[int] = None) -> list[dict[str, Any]]:
        params = {}
        if offset is not None:
            params["offset"] = str(offset)
        query = urllib.parse.urlencode(params)
        method = "getUpdates" + (f"?{query}" if query else "")
        return self.api(method).get("result", [])


SOURCE_LABELS = {"linkedin": "LinkedIn"}


def format_source(vacancy: Vacancy) -> str:
    label = SOURCE_LABELS.get(vacancy.source, vacancy.source)
    subscription = vacancy.raw.get("subscription")
    return f"{label} · подписка «{subscription}»" if subscription else label


def format_location(vacancy: Vacancy) -> str:
    parts = [x for x in (vacancy.location, vacancy.raw.get("work_format")) if x]
    return " · ".join(parts) or ("remote" if vacancy.remote else "не указано")


def format_vacancy_message(vacancy: Vacancy, result: MatchResult) -> str:
    published = vacancy.published_at or "не указана"
    location = format_location(vacancy)
    reasons = "\n".join(f"- {x}" for x in result.reasons[:3]) or "- нет сильных совпадений"
    gaps = "\n".join(f"- {x}" for x in (result.rejects + result.gaps)[:4]) or "- явных пробелов не найдено"
    score = f"\nОценка: {result.score} (для информации, на отправку не влияет)" if vacancy.source == "linkedin" else ""
    marker = "[Пограничная] " if BORDERLINE in result.criteria.values() else ""
    recommendation = result.recommendation
    if vacancy.source == "linkedin" and recommendation == "пропустить":
        # LinkedIn jobs are always sent, so "skip" would contradict the message itself.
        recommendation = "к сведению"
    return (
        f"{marker}{vacancy.title}\n"
        f"{vacancy.company or 'Компания не указана'}\n"
        f"{vacancy.url}\n\n"
        f"Источник: {format_source(vacancy)}\n"
        f"Дата публикации: {published}\n"
        f"Локация/формат: {location}\n\n"
        f"Почему подходит:\n{reasons}\n\n"
        f"Пробелы/риски:\n{gaps}\n\n"
        f"Рекомендация: {recommendation}"
        f"{score}"
    )
