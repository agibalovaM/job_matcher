import argparse
import re
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from job_matcher import cli
from job_matcher.config import Settings
from job_matcher.models import Vacancy


def vacancy(n: int) -> Vacancy:
    return Vacancy(source="fake", source_id=str(n), title="Delivery Manager", company="Acme", url=f"https://example.com/{n}")


class MonitorSourcesTests(unittest.TestCase):
    """monitor-once runs every entry of MONITOR_SOURCES; one failing source does not stop the others."""

    def run_monitor(self, sources):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = Settings(db_path=f"{tmp.name}/jobs.sqlite")
        args = argparse.Namespace(notify=False, limit=1, pages=1, headless=True)
        out = io.StringIO()
        with mock.patch.object(cli, "MONITOR_SOURCES", sources), mock.patch.object(cli.Settings, "from_env", return_value=settings), redirect_stdout(out):
            cli.cmd_monitor_once(args)
        return out.getvalue()

    def test_new_source_is_one_list_entry(self):
        def monitor_fake(app, settings, args):
            seen, sent = app.process_vacancies([vacancy(1), vacancy(2)], notify=args.notify)
            return 2, seen, sent

        output = self.run_monitor([("fake", monitor_fake)])
        self.assertIn("monitor fake: fetched=2, new=2, sent=0", output)
        self.assertIn("monitor total: fetched=2, new=2, sent=0", output)

    def test_failing_and_unconfigured_sources_do_not_stop_others(self):
        def broken(app, settings, args):
            raise RuntimeError("mailbox down")

        def ok(app, settings, args):
            seen, sent = app.process_vacancies([vacancy(3)], notify=False)
            return 1, seen, sent

        output = self.run_monitor([("broken", broken), ("off", lambda *a: None), ("ok", ok)])
        self.assertIn("monitor broken failed: RuntimeError: mailbox down", output)
        self.assertIn("monitor off: skipped=not_configured", output)
        self.assertIn("monitor ok: fetched=1, new=1, sent=0", output)

    def test_registered_sources(self):
        self.assertEqual([name for name, _ in cli.MONITOR_SOURCES], ["hh", "linkedin"])


class RemovedHHApiTests(unittest.TestCase):
    """The official hh.ru API source (hh-once, serve) is removed; bot commands are kept but not wired."""

    def test_cli_has_no_hh_api_commands(self):
        commands = set(cli.build_parser()._subparsers._group_actions[0].choices)
        self.assertNotIn("hh-once", commands)
        self.assertNotIn("serve", commands)
        self.assertTrue({"monitor-once", "hh-browser-once", "linkedin-once", "install-launchd"} <= commands)

    def test_bot_module_kept_but_not_wired(self):
        import pathlib

        from job_matcher import telegram_bot

        self.assertTrue(callable(telegram_bot.handle_telegram_commands))
        package = pathlib.Path(cli.__file__).parent
        imports_bot = re.compile(r"^\s*(from\s+\S*telegram_bot\s+import|import\s+\S*telegram_bot\b|from\s+\.\s+import\s+.*\btelegram_bot\b)", re.M)
        importers = [f.name for f in package.glob("*.py") if f.name != "telegram_bot.py" and imports_bot.search(f.read_text())]
        self.assertEqual(importers, [])

    def test_hh_browser_login_default_query_unchanged(self):
        from job_matcher.app import HH_BROWSER_QUERIES

        self.assertEqual(HH_BROWSER_QUERIES[0], "IT Project Manager")


class HHQueriesTests(unittest.TestCase):
    def test_seven_title_queries_incl_russian(self):
        from job_matcher.app import HH_BROWSER_QUERIES

        self.assertEqual(HH_BROWSER_QUERIES, [
            "IT Project Manager", "Project Manager", "Delivery Manager", "Technical Project Manager",
            "Руководитель проектов", "Менеджер проектов", "Проектный менеджер",
        ])

    def test_readme_page_count_matches_queries(self):
        import pathlib

        from job_matcher.app import HH_BROWSER_QUERIES

        readme = (pathlib.Path(cli.__file__).parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"{len(HH_BROWSER_QUERIES)} страниц поиска", readme)
        self.assertNotIn("3 страницы поиска", readme)


class NotifyTestSkipsKnownTests(unittest.TestCase):
    def test_hh_browser_notify_test_passes_is_known(self):
        captured = {}

        def fake_search(queries, **kwargs):
            captured.update(kwargs)
            return []

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        args = argparse.Namespace(query="Project Manager", limit=5, headless=True)
        with mock.patch.object(cli, "search_hh_browser", fake_search), \
                mock.patch.object(cli.Settings, "from_env", return_value=Settings(db_path=f"{tmp.name}/jobs.sqlite")), \
                redirect_stdout(io.StringIO()):
            cli.cmd_hh_browser_notify_test(args)
        self.assertTrue(callable(captured.get("is_known")))


class MonitorLogAndHealthTests(unittest.TestCase):
    """Timestamps in the log, emails count for LinkedIn, Telegram on 3 failures in a row and on recovery."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(db_path=f"{self.tmp.name}/jobs.sqlite", telegram_bot_token="x", telegram_chat_id="1")
        self.sent = []
        self.fail = True

    def run_monitor(self, sources, notify=True):
        from job_matcher.telegram import TelegramClient

        args = argparse.Namespace(notify=notify, limit=1, pages=1, headless=True)
        out = io.StringIO()
        with mock.patch.object(cli, "MONITOR_SOURCES", sources), \
                mock.patch.object(cli.Settings, "from_env", return_value=self.settings), \
                mock.patch.object(TelegramClient, "send_message", lambda _, text, chat_id=None: self.sent.append(text)), \
                redirect_stdout(out):
            cli.cmd_monitor_once(args)
        return out.getvalue()

    def flaky(self, app, settings, args):
        if self.fail:
            raise RuntimeError("IMAP down")
        return 0, 0, 0, "emails=2"

    def test_every_line_has_a_timestamp_and_details(self):
        self.fail = False
        output = self.run_monitor([("linkedin", self.flaky)])
        lines = [l for l in output.splitlines() if l.strip()]
        self.assertTrue(lines[0].endswith("monitor start"))
        for line in lines:
            self.assertRegex(line, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ")
        self.assertIn("monitor linkedin: emails=2, fetched=0, new=0, sent=0", output)

    def test_alert_after_three_failures_and_on_recovery(self):
        for _ in range(2):
            self.run_monitor([("linkedin", self.flaky)])
        self.assertEqual(self.sent, [])
        self.run_monitor([("linkedin", self.flaky)])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("«linkedin» не работает 3 запуска подряд", self.sent[0])
        self.assertIn("IMAP down", self.sent[0])
        self.run_monitor([("linkedin", self.flaky)])          # 4th failure: no repeat
        self.assertEqual(len(self.sent), 1)
        self.fail = False
        self.run_monitor([("linkedin", self.flaky)])          # recovered
        self.assertEqual(len(self.sent), 2)
        self.assertIn("«linkedin» снова работает", self.sent[1])
        self.run_monitor([("linkedin", self.flaky)])          # still fine: nothing more
        self.assertEqual(len(self.sent), 2)

    def test_no_alerts_without_notify(self):
        for _ in range(3):
            self.run_monitor([("linkedin", self.flaky)], notify=False)
        self.assertEqual(self.sent, [])

    def test_success_resets_the_counter(self):
        for fail in (True, True, False, True, True):
            self.fail = fail
            self.run_monitor([("linkedin", self.flaky)])
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
