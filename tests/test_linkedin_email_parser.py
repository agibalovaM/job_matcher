import email
import unittest
from email import policy

from job_matcher.sources_linkedin_email import normalize_linkedin_job_url, parse_alert_email, search_criteria
from tests.linkedin_samples import alert_email, job_link

JOBS = [
    ("4100000001", "Project Manager", "Acme Software · Example City, Example Country (Hybrid)"),
    ("4100000002", "Technical Project Manager", "Globex · Example Country (Remote)"),
    ("4100000003", "Delivery Manager", "Initech · European Union (On-site)"),
]


class LinkUrlTests(unittest.TestCase):
    def test_tracking_params_removed(self):
        self.assertEqual(
            normalize_linkedin_job_url(job_link("4100000001")),
            ("4100000001", "https://www.linkedin.com/jobs/view/4100000001"),
        )

    def test_slug_in_path(self):
        url = "https://www.linkedin.com/jobs/view/project-manager-at-acme-4100000009?trk=x"
        self.assertEqual(normalize_linkedin_job_url(url)[0], "4100000009")

    def test_redirect_wrapper(self):
        url = "https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Fwww.linkedin.com%2Fjobs%2Fview%2F4100000007%2F%3FtrackingId%3Dx"
        self.assertEqual(normalize_linkedin_job_url(url)[0], "4100000007")

    def test_non_job_links_ignored(self):
        self.assertIsNone(normalize_linkedin_job_url("https://www.linkedin.com/comm/jobs/alerts?token=x"))
        self.assertIsNone(normalize_linkedin_job_url("https://www.linkedin.com/comm/jobs/search?keywords=pm"))

    def test_search_criteria_multiple_senders(self):
        from datetime import date

        self.assertEqual(
            search_criteria(["a@x.com", "b@x.com"], date(2026, 10, 5)),
            '(SINCE 05-Oct-2026 OR FROM "a@x.com" FROM "b@x.com")',
        )


class SyntheticEmailTests(unittest.TestCase):
    def assert_jobs(self, vacancies):
        self.assertEqual(len(vacancies), 3)
        first, second, third = vacancies
        self.assertEqual(first.source, "linkedin")
        self.assertEqual(first.source_id, "4100000001")
        self.assertEqual(first.title, "Project Manager")
        self.assertEqual(first.company, "Acme Software")
        self.assertEqual(first.location, "Example City, Example Country")
        self.assertEqual(first.raw["work_format"], "Hybrid")
        self.assertFalse(first.remote)
        self.assertEqual(first.url, "https://www.linkedin.com/jobs/view/4100000001")
        self.assertEqual((second.location, second.raw["work_format"], second.remote), ("Example Country", "Remote", True))
        self.assertEqual(third.raw["work_format"], "On-site")
        for v in vacancies:
            self.assertEqual(v.raw["subscription"], "project manager")
            self.assertNotIn("Easy Apply", v.title)
            self.assertNotIn("?", v.url)

    def test_html_part(self):
        self.assert_jobs(parse_alert_email(alert_email("<a@test>", "project manager", JOBS, plain=False)))

    def test_text_fallback_when_html_missing(self):
        self.assert_jobs(parse_alert_email(alert_email("<b@test>", "project manager", JOBS, html=False)))

    def test_text_fallback_when_html_has_no_jobs(self):
        msg = alert_email("<c@test>", "project manager", JOBS, html=False)
        msg.add_alternative("<html><body><p>Broken template</p></body></html>", subtype="html")
        self.assert_jobs(parse_alert_email(msg))

    def test_service_labels_not_in_title(self):
        jobs = [("4100000004", "IT Project Manager Easy Apply", "Acme · Example Country (Remote) · Actively recruiting")]
        msg = alert_email("<d@test>", "it pm", jobs, plain=False)
        (vacancy,) = parse_alert_email(msg)
        self.assertEqual(vacancy.title, "IT Project Manager")
        self.assertEqual(vacancy.location, "Example Country")

    def test_alert_created_confirmation_email(self):
        name = '"project manager" OR "delivery manager" in European Union'
        jobs = [("4100000005", "Project Manager", "Acme · European Union")]
        for kwargs in ({"plain": False}, {"html": False}):
            with self.subTest(**kwargs):
                msg = alert_email("<f@test>", name, jobs, header=f"Your job alert has been created: {name}.", **kwargs)
                (vacancy,) = parse_alert_email(msg)
                self.assertEqual(vacancy.raw["subscription"], name)
                self.assertEqual((vacancy.title, vacancy.company, vacancy.location), ("Project Manager", "Acme", "European Union"))

    def test_unparseable_email_warns_and_returns_empty(self):
        msg = email.message_from_string("Message-ID: <e@test>\nSubject: hi\n\nnothing here", policy=policy.default)
        with self.assertLogs("job_matcher.sources_linkedin_email", level="WARNING") as logs:
            self.assertEqual(parse_alert_email(msg), [])
        self.assertIn("<e@test>", logs.output[0])
        self.assertNotIn("nothing here", logs.output[0])


if __name__ == "__main__":
    unittest.main()
