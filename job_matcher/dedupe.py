from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Vacancy


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "trk",
    "trkEmail",
    "trackingId",
    "ref",
    "refId",
    "midToken",
    "lipi",
    "eBP",
    "recommendedFlavor",
    "originalSubdomain",
}


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in TRACKING_PARAMS])
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "").lower()).strip()


def fingerprint(vacancy: Vacancy) -> str:
    url = normalize_url(vacancy.url)
    if url:
        base = url
    else:
        description = normalize_text(vacancy.description)[:1200]
        base = "|".join([
            normalize_text(vacancy.title),
            normalize_text(vacancy.company),
            description,
        ])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
