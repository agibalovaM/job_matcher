import argparse
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


if __name__ == "__main__":
    unittest.main()
