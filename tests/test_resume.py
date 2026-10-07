import tempfile
import unittest
from pathlib import Path

from job_matcher.models import CandidateProfile
from job_matcher.resume import profile_from_resume


class ResumeImportTests(unittest.TestCase):
    """Audit #2: English level is no longer guessed from resume substrings."""

    def test_text_imported_english_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resume.txt"
            path.write_text("B2B SaaS delivery. Advanced analytics. Upper-intermediate team lead.", encoding="utf-8")
            profile = profile_from_resume(str(path))
        self.assertIn("B2B SaaS delivery", profile.raw_text)
        self.assertEqual(profile.english_level, CandidateProfile().english_level)


if __name__ == "__main__":
    unittest.main()
