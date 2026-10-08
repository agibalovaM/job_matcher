from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable, List, Optional, Tuple

from .dedupe import normalize_text
from .models import CandidateProfile, MatchResult, Vacancy


ROLE_TERMS = [
    "project manager",
    "delivery manager",
    "technical project manager",
    "it project manager",
    "program manager",
    "руководитель проект",
    "менеджер проект",
]
# Russian role titles with words in between: "Руководитель IT-проектов", "Проектный менеджер", "Менеджер по проектам".
ROLE_TITLE_PATTERNS = [
    re.compile(r"(?<![^\W_])руководител\w*\s+(?:(?:it|ит)[-\s]+)?проект"),
    re.compile(r"(?<![^\W_])менеджер\w*\s+(?:(?:it|ит)[-\s]+)?проект"),
    re.compile(r"(?<![^\W_])менеджер\w*\s+по\s+(?:(?:it|ит)[-\s]+)?проект"),
    re.compile(r"(?<![^\W_])проектн\w*\s+менеджер"),
    # "Проект-менеджер", "Проджект менеджер", "Delivery менеджер", "Project-manager", "Project & Product Manager".
    re.compile(r"(?<![^\W_])(?:проект|проджект|project|delivery|деливери)[-\s]*(?:менеджер\w*|managers?(?![^\W_]))"),
    re.compile(r"(?<![^\W_])project\s*(?:&|/|and|и)\s*\w+\s+managers?(?![^\W_])"),
]
# Signs that a vacancy is about IT (title or description). Russian entries are stems.
IT_TERMS = [
    "программн", "веб", "мобильн", "приложени", "бэкенд", "бекенд", "фронтенд",
    "software", "saas", "web", "mobile", "app", "backend", "frontend", "api", "qa", "jira", "agile", "scrum",
    "sdlc", "devops", "developer", "development", "ai", "ml", "crm", "erp", "cloud", "platform",
    "платформ", "цифров",
]
# "разработ…" and "продукт…" only in an IT sense: "разработка продукта" and "косметические продукты" are not IT.
IT_PHRASES = re.compile(
    r"(?<![^\W_])(?:разработчик\w*|(?:команд|отдел)\w*\s+разработк\w*"
    r"|разработк\w*\s+(?:по|программ\w*|сайт\w*|приложени\w*|сервис\w*|web|веб\w*|мобильн\w*|backend|frontend|игр\w*)"
    r"|(?:it|ит|цифров\w*|digital|saas|b2b|b2c)[-\s]+продукт\w*|продуктов\w*\s+(?:команд\w*|компани\w*|разработк\w*))"
)
MIN_DESCRIPTION_FOR_IT_CHECK = 100
# "IT"/"ИТ" only in capitals as a separate word: lowercase "it" is an English pronoun, "ит" starts "итог".
IT_ABBREVIATION = re.compile(r"(?<![^\W_])(?:IT|ИТ)(?![^\W_])")
TASK_TERMS = ["requirements", "требован", "risk", "риск", "stakeholder", "подряд", "contractor", "subcontractor", "sdlc", "web", "agile"]
REMOTE_TERMS = ["remote", "удален", "удалён", "удаленная", "удалённая", "дистанционно", "remotely"]
# Remote from anywhere is fine for everyone. Places and regions specific to the candidate come from the profile.
WORLDWIDE_TERMS = ["worldwide", "global", "anywhere", "из любой страны", "из любой точки мира", "из любой точки земного шара"]
REMOTE_BAD = [
    "only us",
    "us only",
    "only uk",
    "uk only",
    "только рф",
    "только россия",
    "must be based in",
    "только из ",
    "из любой точки россии",
    "из любой точки рф",
    "на территории рф",
    "на территории россии",
]
RESTRICTION_LOOKAHEAD = 40
STRICT_INDUSTRIES = ["fintech", "igaming", "gambling", "betting", "business intelligence", "video production"]
# An industry mentioned after one of these (and no requirements header in between) is optional.
OPTIONAL_MARKERS = re.compile(
    r"будет плюсом|плюсом|\bплюс\b|желательно|преимуществ|nice[ -]to[ -]have|preferred|\bplus\b"
)
REQUIRED_HEADERS = re.compile(
    r"требования|requirements|обязательн|must[ -]have|required|мы ожидаем|что мы ждем|что мы ждём|необходим"
)
OPTIONAL_LOOKBACK = 200

EXPERIENCE_PATTERNS = [
    r"(\d{1,2})\s*\+\s*(?:years?|лет|года|год)",
    r"(?:от|не менее|более|свыше|at least|minimum(?: of)?|more than|over)\s+(\d{1,2})(?:-?х|-?ти|-?и)?\s*(?:years?|лет|года|год)",
    r"от\s+(\d{1,2})\s+до\s+\d{1,2}\s*(?:лет|года)",
    r"(\d{1,2})\s*(?:–|-|—)\s*\d{1,2}\s*(?:years?|лет|года)",
    r"(\d{1,2})\s*(?:years?|лет|года)\s+(?:of\s+)?(?:experience|опыта)",
]
EXPERIENCE_CONTEXT = re.compile(r"опыт|experience")
BORDERLINE = "borderline"
ENGLISH_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]


@lru_cache(maxsize=None)
def term_regex(term: str) -> "re.Pattern[str]":
    """Match a term as a word, not as a substring.
    English terms are whole words with an optional plural "s" ("risks", "managers"),
    so "riskless", "web3", "Project Managerment" and "we only use" do not match.
    Russian terms are stems ("удален", "требован"), so only the start must be a word boundary."""
    escaped = re.escape(term)  # keep trailing spaces: "только из " must not match "только изредка"
    # "_" separates words too: "rsu_emea" contains the region "emea".
    tail = "" if re.search(r"[а-яё]", term) else r"s?(?![^\W_])"
    return re.compile(rf"(?<![^\W_]){escaped}{tail}")


def has_term(text: str, term: str) -> bool:
    return term_regex(term).search(text) is not None


def has_any(text: str, terms: Iterable[str]) -> bool:
    return any(has_term(text, term) for term in terms)


def title_has_role(title: str) -> bool:
    lowered = normalize_text(title)
    return has_any(lowered, ROLE_TERMS) or any(p.search(lowered) for p in ROLE_TITLE_PATTERNS)


def it_signal(vacancy: Vacancy) -> Optional[str]:
    """First IT sign found in the title or description, or None."""
    raw = " ".join([vacancy.title, vacancy.description])
    abbreviation = IT_ABBREVIATION.search(raw)
    if abbreviation:
        return abbreviation.group(0)
    lowered = normalize_text(raw)
    term = next((term for term in IT_TERMS if has_term(lowered, term)), None)
    if term:
        return term
    phrase = IT_PHRASES.search(lowered)
    return phrase.group(0) if phrase else None


def english_required(text: str) -> Optional[str]:
    levels = re.findall(r"\b(a1|a2|b1|b2|c1|c2)\b", text)
    if "c2" in levels:
        return "C2"
    if "c1" in levels:
        return "C1"
    if "b2" in levels:
        return "B2"
    if "b1" in levels:
        return "B1"
    if "fluent english" in text or "advanced english" in text:
        return "C1"
    if "upper-intermediate english" in text:
        return "B2"
    return None


def is_optional_mention(text: str, position: int) -> bool:
    # "experience in fintech is a plus": the marker may close the same clause.
    clause_end = re.split(r"[.;\n]", text[position:position + 80], maxsplit=1)[0]
    if OPTIONAL_MARKERS.search(clause_end):
        return True
    window = text[max(0, position - OPTIONAL_LOOKBACK):position]
    optional = [m.end() for m in OPTIONAL_MARKERS.finditer(window)]
    if not optional:
        return False
    required = [m.end() for m in REQUIRED_HEADERS.finditer(window)]
    return not required or optional[-1] > required[-1]


def has_explicit_strict_industry_requirement(text: str) -> Optional[str]:
    for industry in STRICT_INDUSTRIES:
        term = term_regex(industry).pattern
        pattern = rf"(experience|опыт|background).{{0,60}}?(?P<a>{term})|(?P<b>{term}).{{0,60}}(experience|опыт|background)"
        for match in re.finditer(pattern, text):
            position = match.start("a") if match.group("a") else match.start("b")
            if not is_optional_mention(text, position):
                return industry
    return None


def required_experience_years(text: str) -> Optional[int]:
    found = []
    for pattern in EXPERIENCE_PATTERNS:
        for match in re.finditer(pattern, text):
            around = text[max(0, match.start() - 80):match.end() + 80]
            if EXPERIENCE_CONTEXT.search(around):
                found.append(int(match.group(1)))
    found = [n for n in found if 0 < n <= 20]
    return max(found) if found else None


def acceptable_places(profile: CandidateProfile) -> List[str]:
    """Places where on-site/hybrid work is fine: profile places, location and work permits."""
    places: List[str] = []
    for value in [*profile.places, *profile.location.split(","), *profile.work_permits]:
        value = normalize_text(value)
        if len(value) >= 3 and value not in places:
            places.append(value)
    return places


def home_label(profile: CandidateProfile) -> str:
    return ", ".join(profile.work_permits) or profile.location or "не указаны"


def restriction(joined: str, places: List[str]) -> Optional[str]:
    """First country/city restriction that excludes the candidate.
    "только из Лиссабона" for a candidate in Lisbon names an acceptable place, so it is not a restriction."""
    for phrase in REMOTE_BAD:
        for match in term_regex(phrase).finditer(joined):
            after = joined[match.end():match.end() + RESTRICTION_LOOKAHEAD]
            if not has_any(after, places):
                return phrase
    return None


def remote_status(vacancy: Vacancy, text: str, title_only: bool, profile: CandidateProfile) -> Tuple[str, Optional[str]]:
    joined = " ".join([text, normalize_text(vacancy.location)])
    places = acceptable_places(profile)
    regions = [normalize_text(r) for r in profile.remote_regions if normalize_text(r)]
    home = home_label(profile)
    bad = restriction(joined, places)
    if bad:
        return "reject", f"Удалёнка ограничена страной/городом («{bad.strip()}»)"
    # A structured work format from the source (hh "Формат работы") wins over free text.
    work_formats = vacancy.raw.get("work_formats")
    if work_formats:
        remote = "remote" in work_formats
    else:
        remote = vacancy.remote or has_any(joined, REMOTE_TERMS)
    if not remote:
        if has_any(joined, places):
            return "ok", None
        return "reject", f"Офис/гибрид вне ваших локаций ({home})"
    if has_any(joined, places + regions + WORLDWIDE_TERMS):
        return "ok", None
    if title_only:
        # LinkedIn puts the allowed region into the location: "Germany (Remote)" is remote within Germany.
        return "unknown", f"Удалёнка в другой стране — не подтверждено, что можно работать из ваших локаций ({home})"
    return BORDERLINE, f"Пограничная: удалёнка без указания страны — уточните, можно ли из ваших локаций ({home})"


def score_vacancy(vacancy: Vacancy, profile: CandidateProfile) -> MatchResult:
    # Title-only vacancies (e.g. LinkedIn alert emails) have no description: missing requirements
    # are "no data", not a match, and company names ("Global ...") must not count as geography.
    title_only = not normalize_text(vacancy.description)
    if title_only:
        text = normalize_text(" ".join([vacancy.title, vacancy.location]))
    else:
        text = normalize_text(" ".join([vacancy.title, vacancy.company, vacancy.description, vacancy.location]))
    reasons: List[str] = []
    borderline: List[str] = []
    gaps: List[str] = []
    rejects: List[str] = []
    criteria: dict = {}
    score = 0

    if not title_only:
        # Vacancies with a description (hh): the title must be a target role and the job must be about IT.
        # General queries ("Руководитель проектов") otherwise bring construction, catering, cosmetics...
        if not title_has_role(vacancy.title):
            rejects.append("Название вакансии не совпадает с целевыми ролями (Project/Delivery Manager, руководитель/менеджер проектов)")
            criteria["role_title"] = "reject"
        elif not it_signal(vacancy):
            if len(normalize_text(vacancy.description)) < MIN_DESCRIPTION_FOR_IT_CHECK:
                # The vacancy page did not load (only the city is stored): no data is not "not IT".
                gaps.append("Описание не загрузилось — признаки IT не проверены")
                criteria["it"] = "unknown"
            else:
                rejects.append("Нет признаков IT в названии и описании")
                criteria["it"] = "reject"

    # Same title check as the role rule above, so "Проектный менеджер (IT)" or "Delivery менеджер" also get the points.
    if title_has_role(vacancy.title):
        score += 2
        reasons.append("Роль совпадает с целевым направлением Project/Program/Delivery/Technical PM")
        criteria["role"] = "match"
    else:
        gaps.append("Название роли неочевидно совпадает с целевыми должностями")
        criteria["role"] = "unknown"

    task_hits = [term for term in TASK_TERMS if has_term(text, term)]
    if len(task_hits) >= 2:
        score += 2
        reasons.append("В задачах есть управление требованиями, сроками, рисками, web/SDLC или стейкхолдерами")
        criteria["tasks"] = "match"
    elif title_only:
        gaps.append("Описания нет в письме — откройте вакансию")
        criteria["tasks"] = "unknown"
    else:
        gaps.append("Мало подтверждений по задачам PM/Delivery в описании")
        criteria["tasks"] = "unknown"

    required_english = english_required(text)
    own_english = (profile.english_level or "").strip().upper()
    if title_only and required_english is None:
        criteria["english"] = "unknown"
    elif own_english not in ENGLISH_LEVELS:
        gaps.append("Уровень английского в профиле не указан")
        criteria["english"] = "unknown"
    else:
        # One level above the profile is a gap, two or more is a reject.
        above = ENGLISH_LEVELS.index(required_english) - ENGLISH_LEVELS.index(own_english) if required_english else 0
        if above >= 2:
            rejects.append(f"Требуется English {required_english}, в профиле {own_english}")
            criteria["english"] = "reject"
        elif above == 1:
            gaps.append(f"Требуется English {required_english}, в профиле {own_english}")
            criteria["english"] = "gap"
        else:
            score += 1
            reasons.append(f"Нет требования английского выше вашего ({own_english})")
            criteria["english"] = "match"

    geo, geo_reason = remote_status(vacancy, text, title_only, profile)
    if geo == "reject":
        rejects.append(geo_reason or "Локация не подходит")
        criteria["geography"] = "reject"
    elif geo == "ok":
        score += 2
        reasons.append("География подходит: ваша локация или удалёнка из неё / вашего региона / любой страны")
        criteria["geography"] = "match"
    elif geo == BORDERLINE:
        score += 2
        borderline.append(geo_reason)
        criteria["geography"] = BORDERLINE
    else:
        gaps.append(geo_reason or "География неясна")
        criteria["geography"] = "unknown"

    industry = has_explicit_strict_industry_requirement(text)
    if industry and industry not in [x.lower() for x in profile.industries_confirmed]:
        rejects.append(f"Есть обязательный опыт в узкой отрасли ({industry}), которого нет в профиле")
        criteria["industry"] = "reject"
    else:
        criteria["industry"] = "match" if not industry else "unknown"

    years = profile.experience_years
    # A structured lower bound from the source (hh "Опыт работы: 3–6 лет" -> 3) wins over free text.
    field_years = vacancy.raw.get("experience_min_years")
    if field_years is not None:
        required_years = field_years
        required_label = re.sub(r"^\s*опыт работы\s*:\s*", "", vacancy.raw.get("experience") or "", flags=re.IGNORECASE)
        required_label = required_label or f"{required_years}+ лет"
    else:
        required_years = None if title_only else required_experience_years(text)
        required_label = f"{required_years}+ лет"
    if title_only:
        criteria["level"] = "unknown"
    elif required_years is None or required_years <= years:
        score += 1
        reasons.append(f"Требуемый опыт не выше вашего ({years} года)")
        criteria["level"] = "match"
    elif required_years - years <= 2:
        score += 1
        borderline.append(f"Пограничная: требуется опыт {required_label}, у вас {years}")
        criteria["level"] = BORDERLINE
    else:
        # More than 2 years above: no point, but still flagged rather than dropped.
        borderline.append(f"Пограничная: требуется опыт {required_label}, у вас {years}")
        criteria["level"] = BORDERLINE

    if vacancy.apply_available is False:
        gaps.append("Отклик в источнике недоступен или вакансия закрыта")
        criteria["apply"] = "gap"
    else:
        criteria["apply"] = "match"

    gaps = borderline + gaps
    if rejects:
        recommendation = "пропустить"
    elif gaps:
        recommendation = "уточнить"
    else:
        recommendation = "откликнуться"

    return MatchResult(
        score=score,
        recommendation=recommendation,
        reasons=reasons[:3],
        gaps=gaps[:4],
        rejects=rejects,
        criteria=criteria,
    )
