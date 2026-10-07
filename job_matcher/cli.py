from __future__ import annotations

import argparse
import json
import plistlib
import subprocess
from pathlib import Path
from typing import Callable, Optional, Tuple

from .app import HH_BROWSER_QUERIES, JobMatcherApp, should_notify
from .config import Settings
from .db import Store
from .models import CandidateProfile
from .models import Vacancy
from .resume import profile_from_resume
from .sources_linkedin_email import LinkedInMailbox, LinkedInMailError, linkedin_from_mbox, linkedin_from_text, parse_alert_email
from .sources_hh_browser import HHCaptchaDetected, open_hh_browser_session, search_hh_browser


def cmd_init_db(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init()
    store.set_state("threshold", str(settings.threshold))
    print(f"Initialized {settings.db_path}")


def cmd_import_resume(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init()
    profile = profile_from_resume(args.path)
    store.save_profile(profile)
    print(json.dumps(profile.to_json(), ensure_ascii=False, indent=2))
    print("Edit profile manually with: python -m job_matcher.cli profile > profile.json")
    print("Then save edits with: python -m job_matcher.cli import-profile-json profile.json")


def cmd_import_profile_json(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init()
    with open(args.path, "r", encoding="utf-8") as fh:
        profile = CandidateProfile.from_json(json.load(fh))
    store.save_profile(profile)
    print("Profile updated.")


def cmd_profile(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init()
    print(json.dumps(store.load_profile().to_json(), ensure_ascii=False, indent=2))


def cmd_hh_browser_login(args: argparse.Namespace) -> None:
    query = args.query or HH_BROWSER_QUERIES[0]
    print("Opening hh.ru in a persistent browser profile.")
    print("Log in or complete captcha manually in the browser window. Close the browser when finished.")
    try:
        open_hh_browser_session(query)
    except HHCaptchaDetected as exc:
        print(str(exc))
    else:
        print("Browser session closed.")


def cmd_hh_browser_once(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    app = JobMatcherApp(settings)
    queries = [args.query] if args.query else HH_BROWSER_QUERIES
    try:
        vacancies = search_hh_browser(queries, limit_per_query=args.limit, pages=args.pages, headless=args.headless)
    except HHCaptchaDetected as exc:
        print(f"hh.ru browser stopped: {exc}")
        notify_browser_attention(app, str(exc))
        raise SystemExit(3) from exc
    notify = args.notify
    seen, sent = app.process_vacancies(vacancies, notify=notify)
    mode = "notify" if notify else "baseline/no-notify"
    print(f"hh.ru browser processed: mode={mode}, new={seen}, sent={sent}, fetched={len(vacancies)}")
    print_selection_summary(app, limit=10)
    app.store.set_state("hh_browser_attention_sent", "false")


def notify_browser_attention(app: JobMatcherApp, reason: str) -> None:
    if app.store.get_state("hh_browser_attention_sent", "false") == "true":
        return
    if not app.telegram.enabled:
        return
    text = (
        "hh.ru browser мониторинг остановился и требует ручного действия.\n\n"
        f"Причина: {reason}\n\n"
        "Откройте локально: .venv/bin/python -m job_matcher.cli hh-browser-login\n"
        "Войдите в hh.ru или пройдите капчу вручную. Я не буду обходить капчу и не читаю cookies."
    )
    try:
        app.telegram.send_message(text)
        app.store.set_state("hh_browser_attention_sent", "true")
    except Exception as exc:
        print(f"failed to send hh.ru attention notification: {exc}")


def cmd_hh_browser_notify_test(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    app = JobMatcherApp(settings)
    query = args.query or HH_BROWSER_QUERIES[0]
    try:
        vacancies = search_hh_browser([query], limit_per_query=args.limit, pages=1, headless=args.headless)
    except HHCaptchaDetected as exc:
        print(f"hh.ru browser stopped: {exc}")
        raise SystemExit(3) from exc

    from .scoring import score_vacancy

    profile = app.store.load_profile()
    threshold = int(app.store.get_state("threshold", str(settings.threshold)))
    selected = []
    for vacancy in vacancies:
        result = score_vacancy(vacancy, profile)
        if result.passed and result.score >= threshold:
            vacancy.source_id = f"notify-test-{vacancy.source_id}"
            vacancy.url = vacancy.url + ("&" if "?" in vacancy.url else "?") + "job_matcher_notify_test=1"
            selected.append(vacancy)
            break
    if not selected:
        print(f"hh.ru browser notify-test: no passing vacancy found in fetched={len(vacancies)}")
        return
    seen, sent = app.process_vacancies(selected, notify=True)
    print(f"hh.ru browser notify-test processed: new={seen}, sent={sent}, fetched={len(vacancies)}")
    print_selection_summary(app, limit=1)


def cmd_linkedin_once(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    app = JobMatcherApp(settings)
    notify = args.notify and not args.no_notify
    mode = "notify" if notify else "baseline/no-notify"
    if settings.linkedin_mbox_path:
        vacancies = linkedin_from_mbox(settings.linkedin_mbox_path, settings.linkedin_senders)
        seen, sent = app.process_vacancies(vacancies, notify=notify)
        print(f"LinkedIn mbox processed: mode={mode}, new={seen}, sent={sent}, fetched={len(vacancies)}")
    if not linkedin_imap_configured(settings):
        print("LinkedIn IMAP skipped: LINKEDIN_IMAP_USER / LINKEDIN_IMAP_PASSWORD are not configured.")
        return
    try:
        emails, fetched, seen, sent = app.process_linkedin_mailbox(LinkedInMailbox.from_settings(settings), notify=notify)
    except LinkedInMailError as exc:
        print(f"LinkedIn IMAP failed: {exc}")
        raise SystemExit(5) from exc
    print(f"LinkedIn alerts processed: mode={mode}, emails={emails}, new={seen}, sent={sent}, fetched={fetched}")
    if notify:
        print(f"retried failed notifications: sent={app.retry_failed_notifications()}")


def linkedin_imap_configured(settings: Settings) -> bool:
    return bool(settings.linkedin_imap_host and settings.linkedin_imap_user and settings.linkedin_imap_password)


def cmd_linkedin_notify_test(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    app = JobMatcherApp(settings)
    vacancies = []
    if settings.linkedin_mbox_path:
        vacancies.extend(linkedin_from_mbox(settings.linkedin_mbox_path, settings.linkedin_senders))
    if linkedin_imap_configured(settings) and not vacancies:
        # Re-read recent emails, including already processed ones; nothing is marked processed here.
        with LinkedInMailbox.from_settings(settings) as mailbox:
            for alert in mailbox.new_messages(lambda _: False):
                vacancies.extend(parse_alert_email(alert.message))
    selected = select_passing_test_vacancy(app, settings, vacancies, "linkedin-notify-test")
    if not selected:
        print(f"LinkedIn notify-test: no passing vacancy found in fetched={len(vacancies)}")
        return
    seen, sent = app.process_vacancies([selected], notify=True)
    print(f"LinkedIn notify-test processed: new={seen}, sent={sent}, fetched={len(vacancies)}")


def select_passing_test_vacancy(app: JobMatcherApp, settings: Settings, vacancies: list[Vacancy], prefix: str) -> Optional[Vacancy]:
    from .scoring import score_vacancy

    profile = app.store.load_profile()
    threshold = int(app.store.get_state("threshold", str(settings.threshold)))
    for vacancy in vacancies:
        if app.store.is_notified(vacancy.source, vacancy.source_id):
            continue  # show something new, not a duplicate of a real notification
        result = score_vacancy(vacancy, profile)
        if should_notify(vacancy, result, threshold):
            vacancy.source_id = f"{prefix}-{vacancy.source_id}"
            vacancy.url = vacancy.url + ("&" if "?" in vacancy.url else "?") + f"job_matcher_{prefix}=1"
            return vacancy
    return None


def cmd_telegram_test(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    app = JobMatcherApp(settings)
    app.telegram.send_message("Тестовое уведомление Marina Job Matcher: Telegram подключён.")
    print("Telegram test notification sent.")


def cmd_telegram_chat_id(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    app = JobMatcherApp(settings)
    updates = app.telegram.get_updates()
    chats = {}
    for update in updates:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is not None:
            label = chat.get("username") or chat.get("first_name") or chat.get("title") or "private chat"
            chats[str(chat_id)] = label
    if not chats:
        print("No Telegram updates found. Send any message to your bot, then run this command again.")
        return
    print("Available Telegram chats:")
    for chat_id, label in chats.items():
        print(f"- TELEGRAM_CHAT_ID={chat_id} ({label})")


def cmd_test_vacancy(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    app = JobMatcherApp(settings)
    vacancy = Vacancy(
        source="manual-test",
        source_id="telegram-pipeline-test",
        title="Delivery Manager",
        company="Test Company",
        url="https://example.com/jobs/delivery-manager-pipeline-test",
        description=(
            "Remote, work from anywhere. Project management, requirements, risks, "
            "stakeholders, SDLC, web products. No English requirements."
        ),
        location="Remote",
        remote=True,
        apply_available=True,
    )
    seen, sent = app.process_vacancies([vacancy], notify=not args.no_notify)
    print(f"test vacancy processed: new={seen}, sent={sent}")


def cmd_ingest_alert(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    app = JobMatcherApp(settings)
    with open(args.path, "r", encoding="utf-8") as fh:
        text = fh.read()
    vacancies = linkedin_from_text(text)
    seen, sent = app.process_vacancies(vacancies, notify=not args.no_notify)
    print(f"{args.source} alert processed: new={seen}, sent={sent}, fetched={len(vacancies)}")


def cmd_status(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init()
    print(f"DB: {settings.db_path}")
    print(f"Paused: {store.get_state('paused', 'false')}")
    print(f"Threshold: {store.get_state('threshold', str(settings.threshold))}")
    print(f"Poll interval: {settings.poll_interval_seconds}s (launchd runs monitor-once at most every 300s)")
    print("LinkedIn: official Job Alerts only; source delay daily/weekly, local email polling can be frequent")


def print_selection_summary(app: JobMatcherApp, limit: int = 5) -> None:
    rows = app.store.recent(limit)
    if not rows:
        print("selection: no processed vacancies")
        return
    counts = {}
    for row in rows:
        counts[row["recommendation"]] = counts.get(row["recommendation"], 0) + 1
    print("selection criteria: role, tasks, english, geography, industry, level, apply availability")
    print("recent recommendations: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))


def cmd_install_launchd(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    cwd = Path.cwd()
    logs = cwd / "data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    interval = max(300, int(settings.poll_interval_seconds))
    label = "local.marina-job-search.hh-browser"
    plist = {
        "Label": label,
        "ProgramArguments": [
            str(cwd / ".venv" / "bin" / "python"),
            "-m",
            "job_matcher.cli",
            "monitor-once",
            "--pages",
            str(args.pages),
            "--limit",
            str(args.limit),
            "--headless",
            "--notify",
        ],
        "WorkingDirectory": str(cwd),
        "StartInterval": interval,
        "StandardOutPath": str(logs / "hh-browser.out.log"),
        "StandardErrorPath": str(logs / "hh-browser.err.log"),
        "RunAtLoad": False,
    }
    target_dir = Path.home() / "Library" / "LaunchAgents"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{label}.plist"
    target.write_bytes(plistlib.dumps(plist, sort_keys=False))
    subprocess.run(["launchctl", "unload", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "load", str(target)], check=True)
    print(f"launchd installed: {target}")
    print(f"interval_seconds={interval}")
    print(f"logs={logs}")


# Sources polled by monitor-once (launchd). Each returns (fetched, new, sent), or None when not configured.
# To add a source: a module job_matcher/sources_<name>.py that returns list[Vacancy],
# a monitor_<name> function below that passes them to app.process_vacancies, and one entry in MONITOR_SOURCES.
SourceRun = Optional[Tuple[int, int, int]]


def monitor_hh_browser(app: JobMatcherApp, settings: Settings, args: argparse.Namespace) -> SourceRun:
    try:
        vacancies = search_hh_browser(HH_BROWSER_QUERIES, limit_per_query=args.limit, pages=args.pages, headless=args.headless)
    except HHCaptchaDetected as exc:
        print(f"hh.ru browser stopped: {exc}")
        notify_browser_attention(app, str(exc))
        return 0, 0, 0
    # Re-arm the captcha alert once the browser works again.
    app.store.set_state("hh_browser_attention_sent", "false")
    seen, sent = app.process_vacancies(vacancies, notify=args.notify)
    return len(vacancies), seen, sent


def monitor_linkedin(app: JobMatcherApp, settings: Settings, args: argparse.Namespace) -> SourceRun:
    if not linkedin_imap_configured(settings):
        return None
    _, fetched, seen, sent = app.process_linkedin_mailbox(LinkedInMailbox.from_settings(settings), notify=args.notify)
    return fetched, seen, sent


MONITOR_SOURCES: list[Tuple[str, Callable[[JobMatcherApp, Settings, argparse.Namespace], SourceRun]]] = [
    ("hh", monitor_hh_browser),
    ("linkedin", monitor_linkedin),
]


def cmd_monitor_once(args: argparse.Namespace) -> None:
    settings = Settings.from_env()
    app = JobMatcherApp(settings)
    total_fetched = total_new = total_sent = 0
    for name, run in MONITOR_SOURCES:
        try:
            result = run(app, settings, args)
        except Exception as exc:
            # One broken source must not stop the others.
            print(f"monitor {name} failed: {type(exc).__name__}: {exc}")
            continue
        if result is None:
            print(f"monitor {name}: skipped=not_configured")
            continue
        fetched, seen, sent = result
        total_fetched += fetched
        total_new += seen
        total_sent += sent
        print(f"monitor {name}: fetched={fetched}, new={seen}, sent={sent}")
    if args.notify:
        retried = app.retry_failed_notifications()
        total_sent += retried
        print(f"monitor retry: sent={retried}")
    print(f"monitor total: fetched={total_fetched}, new={total_new}, sent={total_sent}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local job matcher for Marina Agibalova")
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("init-db")
    p.set_defaults(func=cmd_init_db)
    p = sub.add_parser("import-resume")
    p.add_argument("path")
    p.set_defaults(func=cmd_import_resume)
    p = sub.add_parser("import-profile-json")
    p.add_argument("path")
    p.set_defaults(func=cmd_import_profile_json)
    p = sub.add_parser("profile")
    p.set_defaults(func=cmd_profile)
    p = sub.add_parser("hh-browser-login")
    p.add_argument("--query")
    p.set_defaults(func=cmd_hh_browser_login)
    p = sub.add_parser("hh-browser-once")
    p.add_argument("--query")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--headless", action="store_true")
    p.add_argument("--notify", action="store_true")
    p.set_defaults(func=cmd_hh_browser_once)
    p = sub.add_parser("hh-browser-notify-test")
    p.add_argument("--query")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--headless", action="store_true")
    p.set_defaults(func=cmd_hh_browser_notify_test)
    p = sub.add_parser("linkedin-once")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--notify", action="store_true")
    p.set_defaults(func=cmd_linkedin_once)
    p = sub.add_parser("linkedin-notify-test")
    p.set_defaults(func=cmd_linkedin_notify_test)
    p = sub.add_parser("telegram-test")
    p.set_defaults(func=cmd_telegram_test)
    p = sub.add_parser("telegram-chat-id")
    p.set_defaults(func=cmd_telegram_chat_id)
    p = sub.add_parser("test-vacancy")
    p.add_argument("--no-notify", action="store_true")
    p.set_defaults(func=cmd_test_vacancy)
    p = sub.add_parser("ingest-alert")
    p.add_argument("source", choices=["linkedin"])
    p.add_argument("path")
    p.add_argument("--no-notify", action="store_true")
    p.set_defaults(func=cmd_ingest_alert)
    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("monitor-once")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--headless", action="store_true")
    p.add_argument("--notify", action="store_true")
    p.set_defaults(func=cmd_monitor_once)
    p = sub.add_parser("install-launchd")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--pages", type=int, default=1)
    p.set_defaults(func=cmd_install_launchd)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
