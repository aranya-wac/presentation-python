from __future__ import annotations

import re
from typing import Optional


EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
ALLOWED_EXTENSIONS = {".txt", ".docx", ".pdf"}

SLIDE_COUNT_RE = re.compile(r"\b(\d{1,3})\s*[-\s]?slides?\b", re.IGNORECASE)


def extract_slide_count_from_prompt(prompt: str) -> Optional[int]:
    """Return the first '<N> slide(s)' / '<N>-slide' count found in the prompt, else None."""
    if not prompt:
        return None
    match = SLIDE_COUNT_RE.search(prompt)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def validate_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email))


def validate_file_extension(filename: str) -> Optional[str]:
    """Return extension if allowed, else None."""
    import os
    _, ext = os.path.splitext(filename.lower())
    return ext if ext in ALLOWED_EXTENSIONS else None


def validate_password_strength(password: str) -> list[str]:
    """Return list of validation error messages."""
    errors: list[str] = []
    if len(password) < 8:
        errors.append("Password must be at least 8 characters long.")
    if not any(c.isupper() for c in password):
        errors.append("Password must contain at least one uppercase letter.")
    if not any(c.isdigit() for c in password):
        errors.append("Password must contain at least one digit.")
    return errors
