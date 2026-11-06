"""Cleaning text that came from outside (the phone, other modules)."""

import unicodedata


def clean_text(value, limit: int) -> str:
    """Remove control characters and extra spaces, and cut to a length."""
    if not isinstance(value, str):
        return ""
    text = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in value)
    return " ".join(text.split())[:limit]
