import unittest

from job_matcher.sources_linkedin_email import linkedin_from_text


class AlertIngestTests(unittest.TestCase):
    """LinkedIn alert text saved to a file (ingest-alert) or forwarded to the bot."""

    def test_title_company_location_lines(self):
        text = "Delivery Manager\nAcme · Example City, Example Country (Remote)\nhttps://www.linkedin.com/jobs/view/123456789/?trk=x"
        (vacancy,) = linkedin_from_text(text)
        self.assertEqual(vacancy.source, "linkedin")
        self.assertEqual(vacancy.source_id, "123456789")
        self.assertEqual((vacancy.title, vacancy.company, vacancy.location), ("Delivery Manager", "Acme", "Example City, Example Country"))
        self.assertTrue(vacancy.remote)
        self.assertEqual(vacancy.url, "https://www.linkedin.com/jobs/view/123456789")

    def test_bare_link_still_becomes_vacancy(self):
        (vacancy,) = linkedin_from_text("https://www.linkedin.com/jobs/view/project-manager-at-acme-4100000099/")
        self.assertEqual(vacancy.source_id, "4100000099")
        self.assertEqual(vacancy.title, "Вакансия LinkedIn")

    def test_text_without_linkedin_links(self):
        self.assertEqual(linkedin_from_text("Project Manager\nhttps://example.com/jobs/1"), [])


if __name__ == "__main__":
    unittest.main()
