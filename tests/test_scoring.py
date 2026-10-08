import unittest

from job_matcher.models import CandidateProfile, Vacancy
from job_matcher.scoring import score_vacancy


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.profile = CandidateProfile(english_level="B1", remote_regions=["Europe", "European", "EMEA"])

    def test_rejects_c1_english(self):
        vacancy = Vacancy(
            source="test",
            source_id="1",
            title="IT Project Manager",
            company="Acme",
            url="https://example.com/1",
            description="Must have advanced English C1 and project management experience.",
            location="Remote Europe",
            remote=True,
        )
        result = score_vacancy(vacancy, self.profile)
        self.assertIn("english", result.criteria)
        self.assertEqual(result.criteria["english"], "reject")
        self.assertEqual(result.recommendation, "пропустить")

    def test_remote_from_home_country_is_match(self):
        vacancy = Vacancy(
            source="test",
            source_id="2",
            title="Delivery Manager",
            company="Acme",
            url="https://example.com/2",
            description="Remote role from Example Country or Europe. Requirements, stakeholders, risks, SDLC.",
            location="Remote",
            remote=True,
        )
        result = score_vacancy(vacancy, self.profile)
        self.assertEqual(result.criteria["geography"], "match")
        self.assertGreaterEqual(result.score, 4)

    def test_home_country_in_full_description_is_match(self):
        vacancy = Vacancy(
            source="test",
            source_id="2b",
            title="Technical Project Manager",
            company="Acme",
            url="https://example.com/2b",
            description="Можно работать удаленно из Example Country. Требования, риски, SDLC, stakeholders.",
            location="Remote",
            remote=False,
        )
        result = score_vacancy(vacancy, self.profile)
        self.assertEqual(result.criteria["geography"], "match")

    def test_unknown_remote_is_gap_not_match(self):
        vacancy = Vacancy(
            source="test",
            source_id="3",
            title="Project Manager",
            company="Acme",
            url="https://example.com/3",
            description="Office role. Requirements and risks.",
            location="Germany",
            remote=False,
        )
        result = score_vacancy(vacancy, self.profile)
        self.assertIn(result.criteria["geography"], {"unknown", "reject"})
        self.assertTrue(result.gaps or result.rejects)

    def test_moscow_office_is_rejected(self):
        vacancy = Vacancy(
            source="test",
            source_id="3b",
            title="IT Project Manager",
            company="Acme",
            url="https://example.com/3b",
            description="Project management, requirements, risks, SDLC, stakeholders. Office in Moscow.",
            location="Москва",
            remote=False,
        )
        result = score_vacancy(vacancy, self.profile)
        self.assertEqual(result.criteria["geography"], "reject")
        self.assertIn("Офис/гибрид вне ваших локаций (Example Country)", result.rejects)
        self.assertEqual(result.recommendation, "пропустить")

    def test_remote_without_country_is_borderline_and_passes(self):
        vacancy = Vacancy(
            source="test",
            source_id="3c",
            title="Delivery Manager",
            company="Acme",
            url="https://example.com/3c",
            description="Remote role. Requirements, risks, SDLC, stakeholders.",
            location="Remote",
            remote=True,
        )
        result = score_vacancy(vacancy, self.profile)
        self.assertEqual(result.criteria["geography"], "borderline")
        self.assertTrue(result.passed)
        self.assertGreaterEqual(result.score, 4)
        self.assertTrue(result.gaps[0].startswith("Пограничная: удалёнка без указания страны"))
        self.assertEqual(result.recommendation, "уточнить")

    def test_strict_industry_requirement_rejected(self):
        vacancy = Vacancy(
            source="test",
            source_id="4",
            title="Project Manager",
            company="Acme",
            url="https://example.com/4",
            description="Project management, risks. Required experience in iGaming.",
            location="Remote Europe",
            remote=True,
        )
        result = score_vacancy(vacancy, self.profile)
        self.assertEqual(result.criteria["industry"], "reject")

    def test_home_country_location_is_match(self):
        vacancy = Vacancy(
            source="test",
            source_id="5",
            title="Project Manager",
            company="Acme",
            url="https://example.com/5",
            description="Remote. Requirements, risks.",
            location="Example Country",
            remote=True,
        )
        self.assertEqual(score_vacancy(vacancy, self.profile).criteria["geography"], "match")

    def test_russia_only_remote_rejected(self):
        for phrase in ["из любой точки России", "из любой точки РФ", "на территории РФ", "на территории России"]:
            with self.subTest(phrase=phrase):
                vacancy = Vacancy(
                    source="test",
                    source_id="6",
                    title="Руководитель проектов",
                    company="Acme",
                    url="https://example.com/6",
                    description=f"Удалённая работа {phrase}. Требования, риски.",
                    location="Москва",
                    remote=True,
                )
                result = score_vacancy(vacancy, self.profile)
                self.assertEqual(result.criteria["geography"], "reject")
                self.assertFalse(result.passed)

    def title_only(self, title, location, remote=True, company="Acme"):
        return score_vacancy(
            Vacancy(source="linkedin", source_id="7", title=title, company=company, url="u", location=location, remote=remote),
            self.profile,
        )

    def test_title_only_no_free_points(self):
        result = self.title_only("Project Manager", "Germany")
        self.assertEqual((result.criteria["english"], result.criteria["level"]), ("unknown", "unknown"))
        self.assertEqual(result.score, 2)
        self.assertIn("Описания нет в письме — откройте вакансию", result.gaps)

    def test_title_only_role_and_geography_reach_threshold(self):
        self.assertEqual(self.title_only("Project Manager", "Example Country").score, 4)
        self.assertEqual(self.title_only("Program Manager", "Example City, Example Country", remote=False).score, 4)
        self.assertEqual(self.title_only("Technical Project Manager", "European Union").score, 4)

    def test_title_only_below_threshold(self):
        self.assertLess(self.title_only("Senior Project Manager", "United States").score, 4)
        self.assertLess(self.title_only("Delivery Lead", "Example City, Example Country", remote=False).score, 4)

    def test_title_only_company_name_is_not_geography(self):
        result = self.title_only("Project Manager", "Germany", company="Global Solutions")
        self.assertEqual(result.criteria["geography"], "unknown")


if __name__ == "__main__":
    unittest.main()


class ScoringRulesV2Tests(unittest.TestCase):
    """Industry 'nice to have', office/hybrid outside Example Country, borderline remote and experience."""

    def setUp(self):
        self.profile = CandidateProfile()

    def score(self, description, location="Москва", remote=True, title="IT Project Manager"):
        vacancy = Vacancy(source="hh-browser", source_id="x", title=title, company="Acme", url="https://example.com/x",
                          description=description, location=location, remote=remote)
        return score_vacancy(vacancy, self.profile)

    # Industry
    def test_industry_nice_to_have_is_not_required(self):
        for phrase in [
            "будет плюсом: умение писать запросы на sql. опыт в fintech тоже пригодится.",
            "опыт управления проектами от двух лет, желательно в fintech",
            "преимуществом станет опыт в fintech или в банках",
            "nice to have: experience in igaming",
            "preferred: fintech experience",
            "experience in fintech is a plus",
        ]:
            with self.subTest(phrase=phrase):
                self.assertNotEqual(self.score(phrase).criteria["industry"], "reject")

    def test_industry_required_still_rejects(self):
        result = self.score("Требования: опыт работы в fintech от 3 лет; знание домена fintech.")
        self.assertEqual(result.criteria["industry"], "reject")

    def test_requirements_header_after_plus_section_makes_it_required(self):
        result = self.score("будет плюсом: английский. требования: опыт работы в fintech обязателен.")
        self.assertEqual(result.criteria["industry"], "reject")

    def test_power_bi_is_not_an_industry(self):
        result = self.score("нужен опыт с таблицами и дашбордами в power bi для отчётов.")
        self.assertEqual(result.criteria["industry"], "match")

    # Geography
    def test_office_and_hybrid_outside_home_places_rejected(self):
        for description in ["Офис в Москве, график 5/2.", "Гибридный формат: 3 дня в офисе, 2 дня дома."]:
            with self.subTest(description=description):
                result = self.score(description, location="Москва", remote=False)
                self.assertEqual(result.criteria["geography"], "reject")

    def test_office_in_home_place_is_match(self):
        self.assertEqual(self.score("Офис в Example City.", location="Example City", remote=False).criteria["geography"], "match")

    def test_remote_with_country_restriction_rejected(self):
        result = self.score("Удалённая работа только из РФ.", remote=True)
        self.assertEqual(result.criteria["geography"], "reject")

    def test_remote_from_anywhere_is_match(self):
        result = self.score("офис в москве по желанию, можно удалённо из любой точки мира", remote=True)
        self.assertEqual(result.criteria["geography"], "match")

    # Experience
    def test_experience_one_or_two_years_more_keeps_point_and_is_borderline(self):
        base = self.score("Удалённо из Example Country. Требования, риски, SDLC.", remote=True)
        for phrase in ["опыт от 4 лет", "5+ years of experience"]:
            with self.subTest(phrase=phrase):
                result = self.score(f"Удалённо из Example Country. Требования, риски, SDLC. {phrase}", remote=True)
                self.assertEqual(result.criteria["level"], "borderline")
                self.assertEqual(result.score, base.score)
                self.assertTrue(any(g.startswith("Пограничная: требуется опыт") for g in result.gaps))

    def test_experience_far_above_is_borderline_without_point(self):
        base = self.score("Удалённо из Example Country. Требования, риски, SDLC.", remote=True)
        result = self.score("Удалённо из Example Country. Требования, риски, SDLC. Опыт работы от 8 лет.", remote=True)
        self.assertEqual(result.criteria["level"], "borderline")
        self.assertEqual(result.score, base.score - 1)
        self.assertIn("Пограничная: требуется опыт 8+ лет, у вас 3", result.gaps)

    def test_experience_within_profile_is_match(self):
        self.assertEqual(self.score("Опыт работы от 3 лет. Удалённо.").criteria["level"], "match")

    def test_unrelated_numbers_are_not_experience(self):
        self.assertEqual(self.score("Компании 15 лет на рынке. Удалённо.").criteria["level"], "match")


class BorderlineCasesTests(unittest.TestCase):
    """Synthetic versions of vacancies that used to be dropped: industry "желательно", remote with more experience."""

    def test_industry_preferred_in_home_country_passes(self):
        vacancy = Vacancy(
            source="hh-browser", source_id="100001", title="IT Project Manager", company="Initech",
            url="https://example.com/vacancy/100001", location="Example Country", remote=True,
            description="Продуктовой компании нужен менеджер проектов: сроки, риски, отчёты для заказчика. "
                        "Требования: опыт управления проектами от двух лет, желательно в fintech; понимание API.",
        )
        result = score_vacancy(vacancy, CandidateProfile())
        self.assertEqual(result.rejects, [])
        self.assertEqual(result.criteria["geography"], "match")
        self.assertGreaterEqual(result.score, 4)

    def test_remote_six_years_passes_as_borderline(self):
        vacancy = Vacancy(
            source="hh-browser", source_id="100002", title="Delivery / Project Manager", company="Hooli",
            url="https://example.com/vacancy/100002", location="Москва", remote=True,
            description="Удалённая работа. Опыт работы в управлении проектами от 6 лет.",
        )
        result = score_vacancy(vacancy, CandidateProfile())
        self.assertEqual(result.rejects, [])
        self.assertEqual((result.criteria["geography"], result.criteria["level"]), ("borderline", "borderline"))
        self.assertGreaterEqual(result.score, 4)

    def test_borderline_marker_in_telegram_message(self):
        from job_matcher.telegram import format_vacancy_message

        vacancy = Vacancy(source="hh-browser", source_id="1", title="Delivery Manager", company="Acme",
                          url="https://hh.ru/vacancy/1", location="Москва", remote=True, description="Удалённая работа.")
        message = format_vacancy_message(vacancy, score_vacancy(vacancy, CandidateProfile()))
        self.assertTrue(message.startswith("[Пограничная] Delivery Manager\n"))
        self.assertIn("Пограничная: удалёнка без указания страны", message)


class PlaceRestrictionTests(unittest.TestCase):
    """Audit #8: "только из <город>" is not a reject when the place suits the profile."""

    def score(self, description, location, remote, profile=None):
        vacancy = Vacancy(source="hh-browser", source_id="r", title="IT Project Manager", company="Acme",
                          url="https://example.com/r", description=description, location=location, remote=remote)
        return score_vacancy(vacancy, profile or CandidateProfile(location="Example City, Example Country", work_permits=["Example Country"]))

    def test_russian_declension_of_profile_place(self):
        lisbon = CandidateProfile(location="Lisbon, Portugal", work_permits=["Portugal"], places=["лиссабон"])
        result = self.score("Офис в Лиссабоне. Рассматриваем только из Лиссабона.", "Лиссабон", remote=False, profile=lisbon)
        self.assertEqual((result.criteria["geography"], result.rejects), ("match", []))

    def test_office_only_from_home_city_is_match(self):
        result = self.score("Офис в Example City. Рассматриваем кандидатов только из Example City.", "Example City", remote=False)
        self.assertEqual(result.criteria["geography"], "match")
        self.assertEqual(result.rejects, [])

    def test_other_city_restriction_still_rejects(self):
        result = self.score("Удалённо, рассматриваем только из Москвы.", "Москва", remote=True)
        self.assertEqual(result.criteria["geography"], "reject")

    def test_place_comes_from_profile(self):
        text = "Remote role. Must be based in Montenegro."
        montenegro = CandidateProfile(location="Podgorica, Montenegro", work_permits=["Montenegro"])
        self.assertNotEqual(self.score(text, "Remote", True, montenegro).criteria["geography"], "reject")
        self.assertEqual(self.score(text, "Remote", True).criteria["geography"], "reject")


class ProfileEnglishTests(unittest.TestCase):
    """Audit #2: the English requirement is compared with profile.english_level."""

    def score(self, description, level):
        vacancy = Vacancy(source="hh-browser", source_id="e", title="IT Project Manager", company="Acme",
                          url="https://example.com/e", description=description, location="Example City", remote=False)
        return score_vacancy(vacancy, CandidateProfile(english_level=level))

    def test_levels_relative_to_profile(self):
        cases = [
            ("English C1 required.", "C1", "match"),
            ("English C1 required.", "B2", "gap"),
            ("English C1 required.", "B1", "reject"),
            ("English B2 required.", "B1", "gap"),
            ("English B2 required.", "A2", "reject"),
            ("English A2 is enough.", "B1", "match"),
            ("No language requirements.", "A1", "match"),
        ]
        for description, level, expected in cases:
            with self.subTest(description=description, level=level):
                self.assertEqual(self.score(description, level).criteria["english"], expected)

    def test_reason_text_uses_profile_level(self):
        result = self.score("English C1 required.", "B2")
        self.assertIn("Требуется English C1, в профиле B2", result.gaps)
        self.assertIn("Нет требования английского выше вашего (C2)", self.score("English C1 required.", "C2").reasons)
        self.assertNotIn("B1", " ".join(result.gaps + result.reasons + result.rejects))

    def test_unknown_profile_level(self):
        result = self.score("English B2 required.", "")
        self.assertEqual(result.criteria["english"], "unknown")
        self.assertIn("Уровень английского в профиле не указан", result.gaps)


class LinkedInRecommendationTests(unittest.TestCase):
    """Audit #9: LinkedIn jobs are always sent, so the message must not say "пропустить"."""

    def test_linkedin_says_for_information(self):
        from job_matcher.telegram import format_vacancy_message

        vacancy = Vacancy(source="linkedin", source_id="1", title="Project Manager", company="Acme",
                          url="https://www.linkedin.com/jobs/view/1", location="Germany", raw={"work_format": "On-site"})
        result = score_vacancy(vacancy, CandidateProfile())
        self.assertEqual(result.recommendation, "пропустить")
        message = format_vacancy_message(vacancy, result)
        self.assertIn("Рекомендация: к сведению", message)
        self.assertNotIn("пропустить", message)

    def test_hh_keeps_recommendation(self):
        from job_matcher.telegram import format_vacancy_message

        vacancy = Vacancy(source="hh-browser", source_id="2", title="Project Manager", company="Acme",
                          url="https://hh.ru/vacancy/2", location="Москва", description="Офис в Москве.")
        self.assertIn("Рекомендация: пропустить", format_vacancy_message(vacancy, score_vacancy(vacancy, CandidateProfile())))


class WordBoundaryTests(unittest.TestCase):
    """Terms match as words, not substrings (audit #15)."""

    def test_english_terms_are_whole_words(self):
        from job_matcher.scoring import has_term

        self.assertFalse(has_term("project managerment office", "project manager"))
        self.assertFalse(has_term("web3 wallet", "web"))
        self.assertFalse(has_term("rest и websocket", "web"))
        self.assertFalse(has_term("a riskless approach", "risk"))
        self.assertFalse(has_term("we only use open source", "only us"))
        self.assertFalse(has_term("cetera", "cet"))

    def test_plural_and_separators_still_match(self):
        from job_matcher.scoring import has_term

        self.assertTrue(has_term("we hire project managers", "project manager"))
        self.assertTrue(has_term("manage risks and stakeholders", "risk"))
        self.assertTrue(has_term("web-products", "web"))
        self.assertTrue(has_term("regional lead team_emea", "emea"))
        self.assertTrue(has_term("open to candidates from the us only", "us only"))
        self.assertTrue(has_term("remote (gmt+1)", "gmt+1"))

    def test_russian_stems_keep_endings(self):
        from job_matcher.scoring import has_term

        self.assertTrue(has_term("работа удалённо", "удалён"))
        self.assertTrue(has_term("сбор требований", "требован"))
        self.assertTrue(has_term("офис в лиссабоне", "лиссабон"))
        self.assertFalse(has_term("заходим в офис только изредка", "только из "))
        self.assertTrue(has_term("рассматриваем только из москвы", "только из "))

    def test_we_only_use_is_not_a_country_restriction(self):
        vacancy = Vacancy(source="hh-browser", source_id="w", title="IT Project Manager", company="Acme",
                          url="https://example.com/w", location="Remote", remote=True,
                          description="Remote from Example Country. We only use open-source tools. Requirements, risks.")
        self.assertEqual(score_vacancy(vacancy, CandidateProfile()).criteria["geography"], "match")

    def test_misspelled_role_is_not_a_role(self):
        vacancy = Vacancy(source="hh-browser", source_id="m", title="Project Managerment Specialist", company="Acme",
                          url="https://example.com/m", location="Example City", remote=False, description="Офис в Example City.")
        self.assertEqual(score_vacancy(vacancy, CandidateProfile()).criteria["role"], "unknown")


class ProfileGeographyTests(unittest.TestCase):
    """Places and remote regions come from the profile, not from the code."""

    def score(self, description, location, remote, profile):
        vacancy = Vacancy(source="hh-browser", source_id="g", title="IT Project Manager", company="Acme",
                          url="https://example.com/g", description=description, location=location, remote=remote)
        return score_vacancy(vacancy, profile)

    def test_office_in_profile_place(self):
        lisbon = CandidateProfile(location="Lisbon, Portugal", work_permits=["Portugal"], places=["Lisbon", "лиссабон"])
        self.assertEqual(self.score("Офис в Лиссабоне.", "Лиссабон", False, lisbon).criteria["geography"], "match")
        self.assertEqual(self.score("Office in Lisbon.", "Lisbon", False, lisbon).criteria["geography"], "match")
        result = self.score("Офис в Мадриде.", "Мадрид", False, lisbon)
        self.assertIn("Офис/гибрид вне ваших локаций (Portugal)", result.rejects)

    def test_remote_regions_from_profile(self):
        text = "Remote, EMEA time zones."
        with_region = CandidateProfile(remote_regions=["EMEA"])
        self.assertEqual(self.score(text, "Remote", True, with_region).criteria["geography"], "match")
        self.assertEqual(self.score(text, "Remote", True, CandidateProfile()).criteria["geography"], "borderline")

    def test_worldwide_needs_no_profile_settings(self):
        result = self.score("Remote, work from anywhere.", "Remote", True, CandidateProfile(places=[], remote_regions=[]))
        self.assertEqual(result.criteria["geography"], "match")

    def test_texts_name_profile_locations(self):
        profile = CandidateProfile(location="Lisbon, Portugal", work_permits=["Portugal"], places=["Lisbon"])
        result = self.score("Remote role.", "Remote", True, profile)
        self.assertIn("Пограничная: удалёнка без указания страны — уточните, можно ли из ваших локаций (Portugal)", result.gaps)


class RoleAndITGateTests(unittest.TestCase):
    """hh vacancies need a target role in the title and an IT sign in the title or description."""

    def score(self, title, description, location="Remote", remote=True, source="hh-browser"):
        vacancy = Vacancy(source=source, source_id="g", title=title, company="Acme", url="https://example.com/g",
                          description=description, location=location, remote=remote)
        return score_vacancy(vacancy, CandidateProfile())

    IT_TEXT = "Удалённо, работа из любой точки мира. Управление командой разработки мобильного приложения, Jira, риски, требования."

    def test_role_spellings_in_title(self):
        from job_matcher.scoring import title_has_role

        for title in ["IT Project Manager", "Delivery менеджер в продуктовую команду", "Проект-менеджер (платформа данных)",
                      "Project-manager (мобильные приложения)", "Project & Delivery Manager (SaaS)", "Руководитель IT-проектов",
                      "Руководитель проектов", "Менеджер проекта", "Менеджер по проектам", "Проектный менеджер",
                      "Проджект менеджер (технический)"]:
            with self.subTest(title=title):
                self.assertTrue(title_has_role(title))
        for title in ["Начальник отдела снабжения (проект)", "Менеджер по оценке проектов",
                      "Менеджер выставочного проекта", "Warehouse Coordinator", "Менеджер по продажам (рекламные проекты)",
                      "Project Managerment Specialist"]:
            with self.subTest(title=title):
                self.assertFalse(title_has_role(title))

    def test_it_signs(self):
        from job_matcher.scoring import it_signal

        def sign(text):
            return it_signal(Vacancy(source="hh-browser", source_id="1", title="PM", company="c", url="u", description=text))

        for text in ["Работа в ИТ-компании", "IT department", "команда разработки", "мы ищем разработчиков",
                     "развитие цифрового продукта", "AI/ML платформа", "Jira и Scrum", "мобильное приложение"]:
            with self.subTest(text=text):
                self.assertIsNotNone(sign(text))
        for text in ["It is a great opportunity.", "Подводим итоги квартала", "следить за разработкой упаковки и выводить товар на рынок",
                     "продукты для дома", "монтаж металлоконструкций"]:
            with self.subTest(text=text):
                self.assertIsNone(sign(text))

    def test_non_it_project_manager_rejected(self):
        result = self.score("Project manager (бытовая химия)", "Удалённо. Работать с фабриками, следить за разработкой упаковки и выводить товар на рынок. " * 3)
        self.assertEqual(result.criteria["it"], "reject")
        self.assertFalse(result.passed)

    def test_non_role_title_rejected(self):
        result = self.score("Начальник отдела снабжения (проект)", self.IT_TEXT)
        self.assertEqual(result.criteria["role_title"], "reject")
        self.assertFalse(result.passed)

    def test_it_project_manager_passes(self):
        result = self.score("Руководитель IT-проектов", self.IT_TEXT)
        self.assertEqual(result.rejects, [])
        self.assertGreaterEqual(result.score, 4)

    def test_missing_description_is_not_rejected_as_non_it(self):
        result = self.score("Product Delivery Manager", "Санкт-Петербург")
        self.assertEqual(result.criteria["it"], "unknown")
        self.assertNotIn("Нет признаков IT в названии и описании", result.rejects)

    def test_linkedin_without_description_not_gated(self):
        result = self.score("Начальник отдела снабжения", "", source="linkedin")
        self.assertNotIn("role_title", result.criteria)
        self.assertNotIn("it", result.criteria)
