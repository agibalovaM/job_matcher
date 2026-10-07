"""LinkedIn Job Alerts from a mailbox (IMAP, read-only).

Works only with email content: never fetches linkedin.com pages.
Never log message bodies or credentials here — only Message-ID, subject and counts.
"""
from __future__ import annotations

import email
import hashlib
import imaplib
import logging
import mailbox
import re
from dataclasses import dataclass
from datetime import date, timedelta
from email.message import Message
from email.utils import parseaddr
from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from .models import Vacancy


log = logging.getLogger(__name__)

SOURCE = "linkedin"
JOB_ID_RE = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#\s]*?-)?(\d{6,})", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>'\"\])]+")
# "Your job alert for X" (regular alert) or "Your job alert has been created: X." (confirmation email).
SUBSCRIPTION_RE = re.compile(r"Your job alert (?:for|has been created:)\s+(.+)", re.IGNORECASE)
FORMAT_RE = re.compile(r"^(.*?)\s*\((Remote|Hybrid|On-site|Onsite|On site)\)\s*$", re.IGNORECASE)
SEPARATOR = "·"
SERVICE_LABELS = (
    "easy apply",
    "actively recruiting",
    "promoted",
    "be an early applicant",
    "early applicant",
    "apply with resume & profile",
    "this company is actively hiring",
    "a new job matches your preferences.",
    "new jobs match your preferences.",
    "new",
    "view job",
    "see all jobs",
)
WORK_FORMATS = {"remote": "Remote", "hybrid": "Hybrid", "on-site": "On-site", "onsite": "On-site", "on site": "On-site"}


class LinkedInMailError(RuntimeError):
    pass


# --- links ---------------------------------------------------------------


def linkedin_job_id(url: str) -> Optional[str]:
    """Return job_id for a LinkedIn job link, including tracking/redirect wrappers."""
    if not url:
        return None
    value = unquote(url.replace("&amp;", "&"))
    match = JOB_ID_RE.search(value)
    if match:
        return match.group(1)
    for values in parse_qs(urlsplit(value).query).values():
        for nested in values:
            match = JOB_ID_RE.search(unquote(nested))
            if match:
                return match.group(1)
    return None


def canonical_job_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}"


def normalize_linkedin_job_url(url: str) -> Optional[Tuple[str, str]]:
    job_id = linkedin_job_id(url)
    if not job_id:
        return None
    return job_id, canonical_job_url(job_id)


# --- text helpers --------------------------------------------------------


def clean_line(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def is_service_label(line: str) -> bool:
    lowered = line.lower().strip(" ·•")
    if lowered in SERVICE_LABELS:
        return True
    return bool(re.fullmatch(r"\d+ (connections?|alumn[ia]|alum|school alumn[ia]|school alum|applicants?).*", lowered))


def strip_service_labels(title: str) -> str:
    result = title
    changed = True
    while changed:
        changed = False
        for label in SERVICE_LABELS:
            pattern = rf"\s*[·•|-]?\s*{re.escape(label)}\s*$"
            if len(label) > 3 and re.search(pattern, result, re.IGNORECASE):
                result = re.sub(pattern, "", result, flags=re.IGNORECASE)
                changed = True
    return clean_line(result)


def split_location(value: str) -> Tuple[str, str]:
    """'Lisbon, Portugal (Remote)' -> ('Lisbon, Portugal', 'Remote')."""
    value = clean_line(value)
    match = FORMAT_RE.match(value)
    if not match:
        return value, ""
    return match.group(1).strip(), WORK_FORMATS[match.group(2).lower()]


def parse_company_location(line: str) -> Tuple[str, str, str]:
    company, _, location = line.partition(SEPARATOR)
    location, work_format = split_location(location)
    return clean_line(company), location, work_format


def find_subscription(text: str) -> str:
    for line in text.splitlines():
        match = SUBSCRIPTION_RE.search(clean_line(line))
        if match:
            return match.group(1).strip(" .:")
    return ""


@dataclass
class ParsedJob:
    job_id: str
    title: str
    company: str = ""
    location: str = ""
    work_format: str = ""


def job_from_lines(job_id: str, lines: Sequence[str]) -> Optional[ParsedJob]:
    """Build a job from card lines: title, then 'Company · Location (Format)'."""
    useful = []
    for line in lines:
        line = clean_line(line)
        if not line or is_service_label(line) or URL_RE.match(line):
            continue
        line = strip_service_labels(line)
        if line:
            useful.append(line)
    separator_at = [i for i, line in enumerate(useful) if SEPARATOR in line and i > 0]
    if separator_at:
        i = separator_at[-1]
        company, location, work_format = parse_company_location(useful[i])
        return ParsedJob(job_id, useful[i - 1], company, location, work_format)
    # Fallback: title, company and location on separate lines.
    if not useful:
        return None
    job = ParsedJob(job_id=job_id, title=useful[0])
    if len(useful) > 1:
        job.company = useful[1]
    if len(useful) > 2:
        job.location, job.work_format = split_location(useful[2])
    return job


# --- HTML ----------------------------------------------------------------


def card_lines(links, job_id: str) -> List[str]:
    """Text of the job card. LinkedIn wraps the whole card (title, 'Company · Location', labels)
    in one <a>; otherwise climb from the title link while the block links only to this job."""
    best = max(links, key=lambda a: len(list(a.stripped_strings)))
    lines = list(best.stripped_strings)
    if any(SEPARATOR in line for line in lines[1:]):
        return lines
    card = best
    for parent in best.parents:
        if parent.name in {"body", "html", "[document]"}:
            break
        if any(linkedin_job_id(a.get("href", "")) != job_id for a in parent.find_all("a", href=True)):
            break
        card = parent
    return list(card.stripped_strings)


def html_subscription(soup) -> str:
    """'Your job alert for <b>name</b>': the phrase and the name may sit in different tags."""
    node = soup.find(string=re.compile(r"Your job alert (?:for|has been created)", re.IGNORECASE))
    element = node.parent if node else None
    for _ in range(3):
        if element is None:
            break
        subscription = find_subscription(clean_line(element.get_text(" ")))
        if subscription:
            return subscription
        element = element.parent
    return ""


def parse_html(html_text: str) -> Tuple[List[ParsedJob], str]:
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["style", "script", "head"]):
        tag.decompose()
    subscription = html_subscription(soup)
    links_by_job: dict[str, list] = {}
    for link in soup.find_all("a", href=True):
        job_id = linkedin_job_id(link["href"])
        if job_id:
            links_by_job.setdefault(job_id, []).append(link)
    jobs = []
    for job_id, links in links_by_job.items():
        job = job_from_lines(job_id, card_lines(links, job_id))
        if job:
            jobs.append(job)
    return jobs, subscription


# --- plain text ----------------------------------------------------------


def parse_text(text: str) -> Tuple[List[ParsedJob], str]:
    subscription = find_subscription(text)
    jobs: dict[str, ParsedJob] = {}
    block: list[str] = []
    for raw_line in text.splitlines():
        line = clean_line(raw_line)
        if not line or set(line) <= set("-=_*") or SUBSCRIPTION_RE.search(line):
            continue
        url_match = URL_RE.search(line)
        if not url_match:
            if not is_service_label(line):
                block.append(line)
            continue
        job_id = linkedin_job_id(url_match.group(0))
        if not job_id:
            continue
        prefix = clean_line(line[: url_match.start()]).rstrip(":")
        if prefix and prefix.lower() not in {"view job", "apply"}:
            block.append(prefix)
        if job_id not in jobs:
            lines = block if any(SEPARATOR in x for x in block) else block[-3:]
            job = job_from_lines(job_id, lines)
            if job:
                jobs[job_id] = job
        block = []
    return list(jobs.values()), subscription


# --- email ---------------------------------------------------------------


def message_part(msg: Message, content_type: str) -> str:
    for part in msg.walk():
        if part.get_content_type() == content_type and part.get_content_disposition() != "attachment":
            payload = part.get_payload(decode=True)
            if payload:
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def message_id_of(msg: Message, raw: bytes = b"") -> str:
    value = clean_line(msg.get("Message-ID", ""))
    if value:
        return value
    return "sha256:" + hashlib.sha256(raw or msg.as_bytes()).hexdigest()


def parse_alert_email(msg: Message) -> List[Vacancy]:
    """Parse one LinkedIn Job Alert email into vacancies. Never raises on bad markup."""
    message_id = message_id_of(msg)
    jobs: List[ParsedJob] = []
    subscription = ""
    html_text = message_part(msg, "text/html")
    if html_text:
        try:
            jobs, subscription = parse_html(html_text)
        except Exception as exc:  # malformed markup must not stop the run
            log.warning("LinkedIn email %s: HTML parse failed (%s)", message_id, type(exc).__name__)
    plain = message_part(msg, "text/plain")
    # The text part names the full alert ("Delivery Manager in Portugal"); HTML only the keywords.
    text_subscription = find_subscription(plain)
    if len(text_subscription) > len(subscription):
        subscription = text_subscription
    if not jobs and plain:
        try:
            jobs, _ = parse_text(plain)
        except Exception as exc:
            log.warning("LinkedIn email %s: text parse failed (%s)", message_id, type(exc).__name__)
    if not jobs:
        log.warning("LinkedIn email %s (%s): no vacancies parsed", message_id, clean_line(msg.get("Subject", ""))[:80])
        return []
    return [job_to_vacancy(job, subscription, msg.get("Date"), {"message_id": message_id}) for job in jobs]


def linkedin_from_text(text: str) -> List[Vacancy]:
    """Alert text pasted into a file or forwarded to the bot (no email headers)."""
    jobs, subscription = parse_text(text)
    found = {job.job_id for job in jobs}
    # A bare link without title lines still counts as a vacancy.
    for url in URL_RE.findall(text):
        job_id = linkedin_job_id(url)
        if job_id and job_id not in found:
            found.add(job_id)
            jobs.append(ParsedJob(job_id=job_id, title="Вакансия LinkedIn"))
    return [job_to_vacancy(job, subscription, None, {"ingested_from": "alert-text"}) for job in jobs]


def job_to_vacancy(job: ParsedJob, subscription: str, published_at: Optional[str], raw: dict) -> Vacancy:
    return Vacancy(
        source=SOURCE,
        source_id=job.job_id,
        title=job.title[:180],
        company=job.company,
        url=canonical_job_url(job.job_id),
        published_at=published_at,
        location=job.location,
        remote=job.work_format == "Remote",
        raw={"subscription": subscription, "work_format": job.work_format, **raw},
    )


def is_from_sender(msg: Message, senders: Iterable[str]) -> bool:
    address = parseaddr(msg.get("From", ""))[1].lower()
    return address in {s.lower() for s in senders}


def linkedin_from_mbox(path: str, senders: Iterable[str]) -> List[Vacancy]:
    vacancies: List[Vacancy] = []
    for msg in mailbox.mbox(path):
        if is_from_sender(msg, senders):
            vacancies.extend(parse_alert_email(msg))
    return vacancies


# --- IMAP ----------------------------------------------------------------


def imap_date(value: date) -> str:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{value.day:02d}-{months[value.month - 1]}-{value.year}"


def search_criteria(senders: Sequence[str], since: date) -> str:
    def from_clause(items: Sequence[str]) -> str:
        if len(items) == 1:
            return f'FROM "{items[0]}"'
        return f"OR {from_clause(items[:1])} {from_clause(items[1:])}"

    return f"(SINCE {imap_date(since)} {from_clause(list(senders))})"


@dataclass
class AlertEmail:
    uid: bytes
    message_id: str
    message: Message


class LinkedInMailbox:
    """Read-only IMAP access. Uses BODY.PEEK so messages stay unread unless mark_as_read is on."""

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        mailbox_name: str,
        senders: Sequence[str],
        since_days: int,
        mark_as_read: bool = False,
        imap_factory: Callable[[str], imaplib.IMAP4] = imaplib.IMAP4_SSL,
    ):
        self.host = host
        self.user = user
        self._password = password
        self.mailbox_name = mailbox_name
        self.senders = list(senders)
        self.since_days = since_days
        self.mark_as_read = mark_as_read
        self.imap_factory = imap_factory
        self.imap: Optional[imaplib.IMAP4] = None

    @classmethod
    def from_settings(cls, settings, imap_factory=imaplib.IMAP4_SSL) -> "LinkedInMailbox":
        return cls(
            settings.linkedin_imap_host,
            settings.linkedin_imap_user,
            settings.linkedin_imap_password,
            settings.linkedin_imap_mailbox,
            settings.linkedin_senders,
            settings.linkedin_since_days,
            settings.linkedin_mark_as_read,
            imap_factory,
        )

    def __enter__(self) -> "LinkedInMailbox":
        try:
            self.imap = self.imap_factory(self.host)
            self.imap.login(self.user, self._password)
        except (imaplib.IMAP4.error, OSError) as exc:
            # Do not include server response: keep credentials-related details out of logs.
            raise LinkedInMailError(f"IMAP login to {self.host} failed ({type(exc).__name__})") from None
        status, _ = self.imap.select(self.mailbox_name, readonly=not self.mark_as_read)
        if status != "OK":
            raise LinkedInMailError(f"IMAP mailbox not found: {self.mailbox_name}")
        return self

    def __exit__(self, *exc) -> None:
        if self.imap is None:
            return
        try:
            self.imap.logout()
        except Exception:
            pass
        self.imap = None

    def _search(self) -> List[bytes]:
        since = date.today() - timedelta(days=self.since_days)
        status, data = self.imap.uid("SEARCH", None, search_criteria(self.senders, since))
        if status != "OK":
            raise LinkedInMailError("IMAP search failed")
        return data[0].split() if data and data[0] else []

    def _fetch(self, uid: bytes, item: str) -> bytes:
        status, data = self.imap.uid("FETCH", uid, f"({item})")
        if status != "OK":
            raise LinkedInMailError(f"IMAP fetch failed for uid {uid.decode()}")
        for chunk in data:
            if isinstance(chunk, tuple):
                return chunk[1]
        return b""

    def new_messages(self, is_processed: Callable[[str], bool]) -> Iterator[AlertEmail]:
        for uid in self._search():
            header = email.message_from_bytes(self._fetch(uid, "BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"))
            message_id = clean_line(header.get("Message-ID", ""))
            if message_id and is_processed(message_id):
                continue
            raw = self._fetch(uid, "BODY.PEEK[]")
            msg = email.message_from_bytes(raw)
            message_id = message_id or message_id_of(msg, raw)
            if is_processed(message_id):
                continue
            yield AlertEmail(uid=uid, message_id=message_id, message=msg)

    def mark_seen(self, uid: bytes) -> None:
        if self.mark_as_read:
            self.imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
