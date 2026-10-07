from __future__ import annotations

from pathlib import Path

from .models import CandidateProfile


def extract_resume_text(path: str) -> str:
    resume_path = Path(path)
    suffix = resume_path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("Install pypdf to parse PDF resumes: python -m pip install -r requirements.txt") from exc
        reader = PdfReader(str(resume_path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError as exc:
            raise RuntimeError("Install python-docx to parse DOCX resumes: python -m pip install -r requirements.txt") from exc
        doc = Document(str(resume_path))
        return "\n".join(p.text for p in doc.paragraphs)
    if suffix in {".txt", ".md"}:
        return resume_path.read_text(encoding="utf-8")
    raise ValueError("Supported resume formats: PDF, DOCX, TXT, MD")


def profile_from_resume(path: str) -> CandidateProfile:
    # Only the text is imported. Fields such as english_level are set by hand via profile JSON:
    # guessing them from substrings was wrong ("B2B" read as English B2).
    return CandidateProfile(raw_text=extract_resume_text(path))
