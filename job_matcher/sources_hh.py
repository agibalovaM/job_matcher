from __future__ import annotations

import html
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Union

from .models import Vacancy


HH_API = "https://api.hh.ru"


class HHApiError(RuntimeError):
    pass


class HHClient:
    def __init__(self, user_agent: str, access_token: str = ""):
        self.user_agent = user_agent
        self.access_token = access_token

    def request(self, path: str, params: Optional[dict[str, Union[str, int, bool]]] = None) -> dict:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{HH_API}{path}" + (f"?{query}" if query else "")
        headers = {"HH-User-Agent": self.user_agent, "User-Agent": self.user_agent}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429:
                retry = exc.headers.get("Retry-After")
                if retry:
                    time.sleep(min(int(retry), 300))
            if exc.code == 403 and '"forbidden"' in body and not self.access_token:
                raise HHApiError(
                    "hh.ru API returned 403 forbidden for anonymous access. "
                    "HH_USER_AGENT is configured, but this run likely needs official hh.ru OAuth "
                    "in HH_ACCESS_TOKEN or captcha resolution in a browser; the app will not bypass it."
                ) from exc
            raise HHApiError(f"hh.ru API error {exc.code}: {body[:1000]}") from exc

    def search(self, queries: Iterable[str], minutes_back: int = 15) -> list[Vacancy]:
        vacancies: dict[str, Vacancy] = {}
        date_from = (datetime.now(timezone.utc) - timedelta(minutes=minutes_back)).isoformat(timespec="seconds")
        for query in queries:
            data = self.request(
                "/vacancies",
                {
                    "text": query,
                    "search_field": "name",
                    "order_by": "publication_time",
                    "per_page": 20,
                    "page": 0,
                    "date_from": date_from,
                    "locale": "RU",
                },
            )
            for item in data.get("items", []):
                vacancy_id = str(item.get("id"))
                if vacancy_id in vacancies:
                    continue
                detail = self.request(f"/vacancies/{vacancy_id}", {"locale": "RU"})
                vacancies[vacancy_id] = vacancy_from_hh(item, detail)
        return list(vacancies.values())


def strip_html(value: str) -> str:
    return html.unescape(value or "").replace("<br />", "\n").replace("<br>", "\n")


def vacancy_from_hh(item: dict, detail: dict) -> Vacancy:
    employer = detail.get("employer") or item.get("employer") or {}
    area = detail.get("area") or item.get("area") or {}
    schedule = detail.get("schedule") or item.get("schedule") or {}
    address = detail.get("address") or {}
    location_bits = [area.get("name") or "", schedule.get("name") or ""]
    if address.get("city"):
        location_bits.append(address["city"])
    return Vacancy(
        source="hh.ru",
        source_id=str(detail.get("id") or item.get("id")),
        title=detail.get("name") or item.get("name") or "",
        company=employer.get("name") or "",
        url=detail.get("alternate_url") or item.get("alternate_url") or "",
        description=strip_html(detail.get("description") or item.get("snippet", {}).get("requirement") or ""),
        published_at=detail.get("published_at") or item.get("published_at"),
        updated_at=detail.get("initial_created_at") or item.get("created_at"),
        location=", ".join(x for x in location_bits if x),
        remote=(schedule.get("id") == "remote"),
        apply_available=not bool(detail.get("archived")),
        raw=detail,
    )
