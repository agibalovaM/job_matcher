"""Telegram bot commands: planned, not wired into any CLI command yet.

Used to run inside the removed `serve` loop (together with the official hh.ru API source).
To enable, call `handle_telegram_commands(app)` periodically, e.g. from `monitor-once`.

Commands: /pause (пауза), /resume (возобновить), /threshold N (порог N),
/latest (показать последние), /why (почему отобрана); a forwarded LinkedIn job link is
parsed and processed like an alert email.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .sources_linkedin_email import linkedin_from_text

if TYPE_CHECKING:
    from .app import JobMatcherApp


def handle_telegram_commands(app: "JobMatcherApp") -> None:
    offset_raw = app.store.get_state("telegram_offset", "")
    offset = int(offset_raw) if offset_raw else None
    for update in app.telegram.get_updates(offset):
        app.store.set_state("telegram_offset", str(update["update_id"] + 1))
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))
        if app.settings.telegram_chat_id and chat_id != str(app.settings.telegram_chat_id):
            continue
        handle_command(app, text, chat_id)


def handle_command(app: "JobMatcherApp", text: str, chat_id: str) -> None:
    lower = text.lower()
    if lower in {"/pause", "пауза"}:
        app.store.set_state("paused", "true")
        app.telegram.send_message("Пауза включена. Новые вакансии будут сохраняться, но не отправляться.", chat_id)
    elif lower in {"/resume", "возобновить"}:
        app.store.set_state("paused", "false")
        app.telegram.send_message("Возобновила уведомления.", chat_id)
    elif lower.startswith("/threshold") or lower.startswith("порог"):
        parts = lower.split()
        if len(parts) == 2 and parts[1].isdigit():
            app.store.set_state("threshold", parts[1])
            app.telegram.send_message(f"Порог уведомлений: {parts[1]}", chat_id)
        else:
            app.telegram.send_message("Формат: /threshold 4", chat_id)
    elif lower in {"/latest", "показать последние"}:
        rows = app.store.recent(5)
        text = "\n\n".join(f"{r['title']} — {r['company']}\n{r['url']}\nscore={r['score']}, {r['recommendation']}" for r in rows)
        app.telegram.send_message(text or "Пока нет обработанных вакансий.", chat_id)
    elif lower.startswith("/why") or lower.startswith("почему отобрана"):
        rows = app.store.recent(1)
        if not rows:
            app.telegram.send_message("Пока нет вакансий для объяснения.", chat_id)
            return
        r = rows[0]
        app.telegram.send_message(
            f"{r['title']} — {r['company']}\ncriteria={r['criteria']}\nreasons={r['reasons']}\ngaps={r['gaps']}\nrejects={r['rejects']}",
            chat_id,
        )
    elif "linkedin.com/jobs/view/" in lower:
        seen, sent = app.process_vacancies(linkedin_from_text(text))
        app.telegram.send_message(f"LinkedIn alert обработан: новых={seen}, отправлено={sent}", chat_id)
