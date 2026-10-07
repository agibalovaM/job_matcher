from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class CandidateProfile:
    name: str = "Марина Агибалова"
    location: str = "Example City, Example Country"
    work_permits: list[str] = field(default_factory=lambda: ["Example Country"])
    # Where on-site/hybrid work is fine. List every spelling used in vacancies:
    # Russian entries are stems, e.g. ["Lisbon", "Portugal", "лиссабон", "португал"].
    places: list[str] = field(default_factory=lambda: ["Example City", "Example Country"])
    # Regions from which remote work is fine besides `places`, e.g. ["Europe", "EMEA", "CET"].
    remote_regions: list[str] = field(default_factory=list)
    # Example values; the real profile lives in the local SQLite database (see README).
    target_roles: list[str] = field(default_factory=lambda: ["Product Manager", "Business Analyst"])
    experience_years: int = 3
    english_level: str = "B2"
    skills: list[str] = field(default_factory=lambda: ["roadmapping", "stakeholder communication", "SQL", "Jira"])
    industries_confirmed: list[str] = field(default_factory=list)
    raw_text: str = ""

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CandidateProfile":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Vacancy:
    source: str
    source_id: str
    title: str
    company: str
    url: str
    description: str = ""
    published_at: Optional[str] = None
    updated_at: Optional[str] = None
    location: str = ""
    remote: bool = False
    apply_available: Optional[bool] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def source_key(self) -> str:
        return f"{self.source}:{self.source_id}"


@dataclass
class MatchResult:
    score: int
    recommendation: str
    reasons: list[str]
    gaps: list[str]
    rejects: list[str]
    criteria: dict[str, str]
    matched_at: str = field(default_factory=lambda: datetime.utcnow().isoformat(timespec="seconds") + "Z")

    @property
    def passed(self) -> bool:
        return not self.rejects
