import unittest

from job_matcher.models import CandidateProfile, Vacancy
from job_matcher.scoring import score_vacancy
from job_matcher.sources_hh_browser import detect_remote, parse_experience_min_years, parse_work_formats


class WorkFormatTests(unittest.TestCase):
    def test_values_from_work_formats_text(self):
        self.assertEqual(parse_work_formats("Формат работы: удалённо"), ["remote"])
        self.assertEqual(parse_work_formats("Формат работы: на месте работодателя"), ["onsite"])
        self.assertEqual(parse_work_formats("Формат работы: гибрид, удалённо"), ["hybrid", "remote"])
        self.assertEqual(parse_work_formats("Формат работы: разъездной"), ["field"])
        # Real hh.ru values join alternatives with "или".
        self.assertEqual(parse_work_formats("Формат работы: на месте работодателя или гибрид"), ["onsite", "hybrid"])
        self.assertEqual(parse_work_formats("Формат работы: удалённо или гибрид"), ["remote", "hybrid"])

    def test_empty_and_unknown(self):
        self.assertEqual(parse_work_formats(""), [])
        self.assertEqual(parse_work_formats("Формат работы: вахта"), ["вахта"])

    def test_field_wins_over_text(self):
        # An on-site vacancy may still mention a remote team or partly working from home.
        self.assertFalse(detect_remote(["onsite"], "координировать удалённую команду разработчиков"))
        self.assertFalse(detect_remote(["onsite", "hybrid"], "после адаптации два дня в офисе, остальное удалённо"))
        self.assertTrue(detect_remote(["remote", "hybrid"], ""))

    def test_text_fallback_without_field(self):
        self.assertTrue(detect_remote([], "Удалённая работа"))
        self.assertFalse(detect_remote([], "Офис в Москве"))


class ExperienceFieldTests(unittest.TestCase):
    def test_lower_bound(self):
        self.assertEqual(parse_experience_min_years("Опыт работы: не требуется"), 0)
        self.assertEqual(parse_experience_min_years("Опыт работы: 1–3 года"), 1)
        self.assertEqual(parse_experience_min_years("Опыт работы: 3–6 лет"), 3)
        self.assertEqual(parse_experience_min_years("Опыт работы: более 6 лет"), 7)
        self.assertIsNone(parse_experience_min_years(""))


class ScoringWithHHFieldsTests(unittest.TestCase):
    def score(self, description, work_formats=None, experience=None, location="Москва", remote=True):
        raw = {}
        if work_formats is not None:
            raw["work_formats"] = work_formats
        if experience is not None:
            raw["experience"] = experience
            raw["experience_min_years"] = parse_experience_min_years(experience)
        vacancy = Vacancy(source="hh-browser", source_id="1", title="IT Project Manager", company="Acme",
                          url="https://hh.ru/vacancy/1", description=description, location=location, remote=remote, raw=raw)
        return score_vacancy(vacancy, CandidateProfile())

    def test_onsite_field_beats_remote_words_in_text(self):
        result = self.score("Координировать удалённую команду.", work_formats=["onsite"])
        self.assertEqual(result.criteria["geography"], "reject")
        self.assertIn("Офис/гибрид вне ваших локаций (Example Country)", result.rejects)

    def test_onsite_field_beats_anywhere_in_text(self):
        result = self.score("Удаленная работа из любой точки мира.", work_formats=["onsite", "hybrid"])
        self.assertEqual(result.criteria["geography"], "reject")

    def test_remote_field_without_country_is_borderline(self):
        self.assertEqual(self.score("Описание без слов про формат.", work_formats=["remote"]).criteria["geography"], "borderline")

    def test_experience_ranges(self):
        for text in ["Опыт работы: не требуется", "Опыт работы: 1–3 года", "Опыт работы: 3–6 лет"]:
            with self.subTest(text=text):
                self.assertEqual(self.score("Удалённо из Example Country.", ["remote"], text).criteria["level"], "match")

    def test_more_than_six_years_is_borderline_without_point(self):
        base = self.score("Удалённо из Example Country.", ["remote"], "Опыт работы: 3–6 лет")
        result = self.score("Удалённо из Example Country.", ["remote"], "Опыт работы: более 6 лет")
        self.assertEqual(result.criteria["level"], "borderline")
        self.assertEqual(result.score, base.score - 1)
        self.assertIn("Пограничная: требуется опыт более 6 лет, у вас 3", result.gaps)

    def test_experience_field_beats_text(self):
        result = self.score("Удалённо из Example Country. Опыт работы от 8 лет.", ["remote"], "Опыт работы: 3–6 лет")
        self.assertEqual(result.criteria["level"], "match")

    def test_without_fields_text_rules_apply(self):
        result = self.score("Удалённо из Example Country. Опыт работы от 8 лет.")
        self.assertEqual(result.criteria["level"], "borderline")


if __name__ == "__main__":
    unittest.main()


class FakeCards:
    def __init__(self, n):
        self.n = n

    def count(self):
        return self.n

    def nth(self, i):
        return i


class FakePage:
    url = "https://hh.ru/search/vacancy?text=x"

    def __init__(self, n):
        self.cards = FakeCards(n)

    def locator(self, selector):
        return self.cards


class HHPageFailureTests(unittest.TestCase):
    """Audit #11: a failed vacancy page skips only that vacancy; #10: missing key fields are logged."""

    def setUp(self):
        from unittest import mock
        from job_matcher import sources_hh_browser as hh

        self.hh = hh
        self.mock = mock
        self.card = lambda card, idx: Vacancy(source="hh-browser", source_id=str(idx), title=f"PM {idx}", company="Acme",
                                               url=f"https://hh.ru/vacancy/{idx}")

    def test_timeout_on_one_page_skips_only_it(self):
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        def enrich(context, vacancy):
            if vacancy.source_id == "1":
                raise PlaywrightTimeoutError("Page.goto: Timeout 60000ms exceeded.")
            return vacancy

        with self.mock.patch.object(self.hh, "vacancy_from_card", self.card), \
                self.mock.patch.object(self.hh, "enrich_vacancy_from_detail", enrich), \
                self.assertLogs("job_matcher.sources_hh_browser", level="WARNING") as logs:
            vacancies = self.hh.extract_vacancies_from_page(None, FakePage(3), limit=10)
        self.assertEqual([v.source_id for v in vacancies], ["0", "2"])
        self.assertIn("https://hh.ru/vacancy/1", logs.output[0])

    def test_captcha_still_stops_the_run(self):
        def enrich(context, vacancy):
            raise self.hh.HHCaptchaDetected("captcha")

        with self.mock.patch.object(self.hh, "vacancy_from_card", self.card), \
                self.mock.patch.object(self.hh, "enrich_vacancy_from_detail", enrich):
            with self.assertRaises(self.hh.HHCaptchaDetected):
                self.hh.extract_vacancies_from_page(None, FakePage(2), limit=10)

    def test_no_cards_is_logged(self):
        with self.assertLogs("job_matcher.sources_hh_browser", level="WARNING") as logs:
            self.assertEqual(self.hh.extract_vacancies_from_page(None, FakePage(0), limit=10), [])
        self.assertIn("no vacancy cards found", logs.output[0])

    def test_missing_key_fields_are_logged(self):
        with self.assertLogs("job_matcher.sources_hh_browser", level="WARNING") as logs:
            missing = self.hh.warn_missing_fields("https://hh.ru/vacancy/5", {
                "description": "text", "work format (work-formats-text)": "", "experience (work-experience-text)": "",
            })
        self.assertEqual(missing, ["work format (work-formats-text)", "experience (work-experience-text)"])
        self.assertIn("work-formats-text", logs.output[0])

    def test_all_fields_present_logs_nothing(self):
        # Python 3.9 has no assertNoLogs.
        with self.mock.patch.object(self.hh.log, "warning") as warning:
            self.assertEqual(self.hh.warn_missing_fields("u", {"description": "x"}), [])
        warning.assert_not_called()


class KnownVacancySkipTests(unittest.TestCase):
    """Vacancies already in the database are skipped without opening their page (less load on hh.ru)."""

    setUp = HHPageFailureTests.setUp

    def test_known_vacancy_page_not_opened(self):
        opened = []

        def enrich(context, vacancy):
            opened.append(vacancy.source_id)
            return vacancy

        with self.mock.patch.object(self.hh, "vacancy_from_card", self.card), \
                self.mock.patch.object(self.hh, "enrich_vacancy_from_detail", enrich):
            vacancies = self.hh.extract_vacancies_from_page(None, FakePage(4), limit=10, is_known=lambda sid: sid in {"0", "2"})
        self.assertEqual(opened, ["1", "3"])
        self.assertEqual([v.source_id for v in vacancies], ["1", "3"])

    def test_callback_counts_known_from_store(self):
        import tempfile

        from job_matcher.app import JobMatcherApp
        from job_matcher.cli import known_hh_vacancy
        from job_matcher.config import Settings

        with tempfile.TemporaryDirectory() as tmp:
            app = JobMatcherApp(Settings(db_path=f"{tmp}/jobs.sqlite"))
            app.process_vacancies([self.card(None, 7)], notify=False)
            counter = {}
            is_known = known_hh_vacancy(app, counter)
            self.assertTrue(is_known("7"))
            self.assertFalse(is_known("8"))
            self.assertEqual(counter, {"known": 1})
            app.store.conn.close()
