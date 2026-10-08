from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable, Iterable, List, Optional
from urllib.parse import urlencode

from .models import Vacancy


HH_BROWSER_PROFILE = ".browser-profiles/hh"

log = logging.getLogger(__name__)


class HHCaptchaDetected(RuntimeError):
    pass


def hh_search_url(query: str, page: int = 0) -> str:
    params = {
        "text": query,
        "search_field": "name",
        "order_by": "publication_time",
        "items_on_page": "20",
        "page": str(page),
    }
    return "https://hh.ru/search/vacancy?" + urlencode(params)


def search_hh_browser(
    queries: Iterable[str],
    profile_dir: str = HH_BROWSER_PROFILE,
    limit_per_query: int = 10,
    pages: int = 1,
    headless: bool = False,
    is_known: Optional[Callable[[str], bool]] = None,
) -> List[Vacancy]:
    """`is_known(source_id)` -> True skips a vacancy already in the database without opening its page."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Install Playwright first: .venv/bin/python -m pip install -r requirements.txt") from exc

    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    vacancies: List[Vacancy] = []
    skip = run_skipper(is_known)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=headless,
            locale="ru-RU",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            for query in queries:
                for page_no in range(max(1, pages)):
                    url = hh_search_url(query, page_no)
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    except PlaywrightError as exc:
                        # Network hiccup on one search page: skip it, keep the other queries.
                        log.warning("hh.ru search page failed (%s), skipped: %s", type(exc).__name__, url)
                        continue
                    page.wait_for_timeout(2500)
                    if is_captcha_page(page):
                        raise HHCaptchaDetected(
                            "hh.ru showed a captcha. Complete it manually in the opened browser window, then rerun."
                        )
                    try:
                        page.wait_for_selector('[data-qa="vacancy-serp__vacancy"], [data-qa="serp-item__title"]', timeout=15000)
                    except PlaywrightTimeoutError:
                        if is_captcha_page(page):
                            raise HHCaptchaDetected(
                                "hh.ru showed a captcha. Complete it manually in the opened browser window, then rerun."
                            )
                    vacancies.extend(extract_vacancies_from_page(context, page, limit_per_query, skip))
        finally:
            context.close()
    return vacancies


def open_hh_browser_session(query: str, profile_dir: str = HH_BROWSER_PROFILE) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Install Playwright first: .venv/bin/python -m pip install -r requirements.txt") from exc

    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            locale="ru-RU",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(hh_search_url(query), wait_until="domcontentloaded", timeout=60000)
        input("After solving the captcha in the browser (if any), press Enter here to close it...")
        context.close()


def is_captcha_page(page) -> bool:
    text = safe_inner_text(page, "body").lower()
    url = page.url.lower()
    return "captcha" in url or "капч" in text or "подтвердите, что вы не робот" in text


def run_skipper(is_known: Optional[Callable[[str], bool]] = None) -> Callable[[str], bool]:
    """Skip a vacancy already in the database, or already seen earlier in this run:
    overlapping queries ("IT Project Manager" is part of "Project Manager") return the same vacancy."""
    seen_in_run: set = set()

    def skip(source_id: str) -> bool:
        if source_id in seen_in_run:
            return True
        seen_in_run.add(source_id)
        return bool(is_known and is_known(source_id))

    return skip


def extract_vacancies_from_page(context, page, limit: int, is_known: Optional[Callable[[str], bool]] = None) -> List[Vacancy]:
    cards = page.locator('[data-qa="vacancy-serp__vacancy"]')
    if cards.count() == 0:
        cards = page.locator('[data-qa="vacancy-serp__vacancy_standard"]')
    vacancies: List[Vacancy] = []
    if cards.count() == 0:
        log.warning("hh.ru search page: no vacancy cards found (empty results or outdated selector): %s", page.url)
    count = min(cards.count(), limit)
    for idx in range(count):
        vacancy = card_or_skip(cards.nth(idx), idx)
        if vacancy is not None and is_known is not None and is_known(vacancy.source_id):
            # Already saved and scored: opening its page again would only add load on hh.ru.
            continue
        if vacancy is not None:
            vacancy = enrich_or_skip(context, vacancy)
        if vacancy is not None:
            vacancies.append(vacancy)
    return vacancies


def card_or_skip(card, idx: int) -> Optional[Vacancy]:
    from playwright.sync_api import Error as PlaywrightError

    try:
        return vacancy_from_card(card, idx)
    except PlaywrightError as exc:
        log.warning("hh.ru search card #%d failed (%s), skipped", idx, type(exc).__name__)
        return None


def enrich_or_skip(context, vacancy: Vacancy) -> Optional[Vacancy]:
    """One slow or broken vacancy page must not lose the rest. A captcha still stops the run.
    A skipped vacancy is not saved, so the next run finds it again."""
    from playwright.sync_api import Error as PlaywrightError

    try:
        return enrich_vacancy_from_detail(context, vacancy)
    except PlaywrightError as exc:  # TimeoutError is a subclass
        log.warning("hh.ru vacancy page failed (%s), skipped until the next run: %s", type(exc).__name__, vacancy.url)
        return None


def vacancy_from_card(card, idx: int) -> Optional[Vacancy]:
    title_link = first_locator(card, [
        '[data-qa="serp-item__title"]',
        'a[data-qa="vacancy-serp__vacancy-title"]',
        'a[href*="/vacancy/"]',
    ])
    if title_link is None:
        return None
    title = clean_text(title_link.inner_text(timeout=3000))
    url = title_link.get_attribute("href") or ""
    company = clean_text_from_first(card, [
        '[data-qa="vacancy-serp__vacancy-employer-text"]',
        '[data-qa="vacancy-serp__vacancy-employer"]',
        '[data-qa="bloko-header-2"]',
    ])
    location = clean_text_from_first(card, [
        '[data-qa="vacancy-serp__vacancy-address"]',
        '[data-qa="vacancy-serp__vacancy-address"] span',
    ])
    compensation = clean_text_from_first(card, [
        '[data-qa="vacancy-serp__vacancy-compensation"]',
    ])
    snippet = clean_text_from_first(card, [
        '[data-qa="vacancy-serp__vacancy_snippet_responsibility"]',
        '[data-qa="vacancy-serp__vacancy_snippet_requirement"]',
    ])
    conditions = "\n".join(x for x in [location, compensation, snippet] if x)
    source_id = vacancy_id_from_url(url) or f"hh-browser-{idx}-{abs(hash(url))}"
    vacancy = Vacancy(
        source="hh-browser",
        source_id=source_id,
        title=title,
        company=company,
        url=url,
        description=conditions,
        location=location,
        remote=is_remote_text(" ".join([title, location, conditions])),
        apply_available=True,
        raw={"extracted_from": "hh.ru browser search"},
    )
    return vacancy


def enrich_vacancy_from_detail(context, vacancy: Vacancy) -> Vacancy:
    if not vacancy.url:
        return vacancy
    detail = context.new_page()
    try:
        detail.goto(vacancy.url, wait_until="domcontentloaded", timeout=60000)
        detail.wait_for_timeout(1200)
        if is_captcha_page(detail):
            raise HHCaptchaDetected(
                "hh.ru showed a captcha. Complete it manually in the opened browser window, then rerun."
            )
        body = safe_inner_text(detail, "body")
        title = clean_text_from_first(detail, [
            '[data-qa="vacancy-title"]',
            "h1",
        ]) or vacancy.title
        company = clean_text_from_first(detail, [
            '[data-qa="vacancy-company-name"]',
            '[data-qa="vacancy-company-name"] span',
            '[data-qa="bloko-header-2"]',
        ]) or vacancy.company
        location = clean_text_from_first(detail, [
            '[data-qa="vacancy-view-location"]',
            '[data-qa="vacancy-view-raw-address"]',
            '[data-qa="vacancy-view-location"] span',
        ]) or vacancy.location
        description = clean_text_from_first(detail, [
            '[data-qa="vacancy-description"]',
            '[data-qa="vacancy-section-description"]',
            ".vacancy-description",
        ])
        salary = clean_text_from_first(detail, [
            '[data-qa="vacancy-salary"]',
            '[data-qa="vacancy-compensation"]',
        ])
        experience = clean_text_from_first(detail, ['[data-qa="work-experience-text"]'])
        # "Формат работы: удалённо, гибрид". The old employment-mode block is kept as a fallback.
        schedule = clean_text_from_first(detail, [
            '[data-qa="work-formats-text"]',
            '[data-qa="vacancy-view-employment-mode"]',
        ])
        published_at = find_first(body, [
            r"Вакансия опубликована\s+([^.\n]+)",
            r"Опубликована\s+([^.\n]+)",
        ])
        updated_at = find_first(body, [
            r"Вакансия обновлена\s+([^.\n]+)",
            r"обновлена\s+([^.\n]+)",
        ])
        unavailable = any(marker in body.lower() for marker in [
            "вакансия в архиве",
            "вакансия недоступна",
            "откликнуться нельзя",
        ])
        warn_missing_fields(vacancy.url, {
            "description": description,
            "work format (work-formats-text)": schedule,
            "experience (work-experience-text)": experience,
        })
        apply_button = detail.locator('[data-qa="vacancy-response-link-top"], [data-qa="vacancy-response-button"], a[href*="/applicant/vacancy_response"]')
        full_description = "\n".join(x for x in [location, salary, schedule, description] if x)
        vacancy.title = title
        vacancy.company = company
        vacancy.location = location
        vacancy.description = full_description or vacancy.description
        work_formats = parse_work_formats(schedule)
        vacancy.remote = detect_remote(work_formats, " ".join([title, location, full_description, body[:2000]]))
        vacancy.published_at = published_at or vacancy.published_at
        vacancy.updated_at = updated_at or vacancy.updated_at
        vacancy.apply_available = (apply_button.count() > 0) and not unavailable
        vacancy.raw.update({
            "detail_opened": True,
            "salary": salary,
            "schedule": schedule,
            "work_formats": work_formats,
            "experience": experience,
            "experience_min_years": parse_experience_min_years(experience),
            "published_text": published_at,
            "updated_text": updated_at,
        })
        return vacancy
    finally:
        detail.close()


def warn_missing_fields(url: str, fields: dict) -> List[str]:
    """Key fields come from hh.ru markup; an empty one usually means a selector went stale."""
    missing = [name for name, value in fields.items() if not value]
    if missing:
        log.warning("hh.ru %s: not found on page (selector may be outdated): %s", url, ", ".join(missing))
    return missing


def first_locator(scope, selectors):
    for selector in selectors:
        loc = scope.locator(selector)
        if loc.count() > 0:
            return loc.first
    return None


def safe_inner_text(page, selector: str) -> str:
    try:
        loc = page.locator(selector)
        if loc.count() == 0:
            return ""
        return loc.first.inner_text(timeout=3000)
    except Exception:
        return ""


def clean_text_from_first(scope, selectors) -> str:
    loc = first_locator(scope, selectors)
    if loc is None:
        return ""
    try:
        return clean_text(loc.inner_text(timeout=3000))
    except Exception:
        return ""


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def find_first(text: str, patterns) -> str:
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            return clean_text(match.group(1))
    return ""


def vacancy_id_from_url(url: str) -> str:
    match = re.search(r"/vacancy/(\d+)", url or "")
    return match.group(1) if match else ""


WORK_FORMATS = {
    "удалённо": "remote",
    "удаленно": "remote",
    "гибрид": "hybrid",
    "на месте работодателя": "onsite",
    "разъездной": "field",
}


def parse_work_formats(text: str) -> List[str]:
    """'Формат работы: удалённо, гибрид' -> ['remote', 'hybrid']; unknown values are kept as is."""
    value = re.sub(r"^\s*формат работы\s*:?", "", (text or "").strip(), flags=re.IGNORECASE)
    formats = []
    for part in re.split(r"[,;]| или ", value):
        part = clean_text(part).lower()
        if part:
            formats.append(WORK_FORMATS.get(part, part))
    return formats


def parse_experience_min_years(text: str) -> Optional[int]:
    """Lower bound of hh 'Опыт работы': 'не требуется' -> 0, '1–3 года' -> 1, '3–6 лет' -> 3, 'более 6 лет' -> 7."""
    value = (text or "").lower()
    if not value:
        return None
    if "не требуется" in value or "без опыта" in value:
        return 0
    more = re.search(r"более\s+(\d+)", value)
    if more:
        return int(more.group(1)) + 1
    number = re.search(r"(\d+)", value)
    return int(number.group(1)) if number else None


def detect_remote(work_formats: List[str], text: str) -> bool:
    """The hh 'Формат работы' field wins; free text ("удалённой командой") is only a fallback."""
    if work_formats:
        return "remote" in work_formats
    return is_remote_text(text)


def is_remote_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(term in lowered for term in ["удален", "удалён", "remote"])
