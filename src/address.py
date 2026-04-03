"""Address normalization and country code conversion."""

import json
import re
from pathlib import Path
from typing import Optional

from unidecode import unidecode

_COUNTRY_MAP: Optional[dict[str, str]] = None
_LEGAL_FORMS: Optional[list[str]] = None

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_country_map() -> dict[str, str]:
    global _COUNTRY_MAP
    if _COUNTRY_MAP is None:
        with open(DATA_DIR / "country_mapping.json", encoding="utf-8") as f:
            _COUNTRY_MAP = json.load(f)
    return _COUNTRY_MAP


def _load_legal_forms() -> list[str]:
    global _LEGAL_FORMS
    if _LEGAL_FORMS is None:
        with open(DATA_DIR / "legal_forms.txt", encoding="utf-8") as f:
            _LEGAL_FORMS = [
                line.strip().lower()
                for line in f
                if line.strip()
            ]
        # Sort longest first so we strip "pty ltd" before "ltd"
        _LEGAL_FORMS.sort(key=len, reverse=True)
    return _LEGAL_FORMS


def country_to_iso(country_name: Optional[str]) -> Optional[str]:
    """Convert a country name (Czech or English) to ISO 3166-1 alpha-2 code."""
    if not country_name:
        return None

    # Already an ISO code?
    cleaned = country_name.strip().upper()
    if len(cleaned) == 2 and cleaned.isalpha():
        return cleaned

    mapping = _load_country_map()
    key = country_name.strip().lower()

    # Try exact match first
    if key in mapping:
        return mapping[key]

    # Try without diacritics
    key_ascii = unidecode(key)
    for k, v in mapping.items():
        if unidecode(k) == key_ascii:
            return v

    return None


def normalize_name(name: str) -> str:
    """Normalize an entity name for matching: lowercase, strip diacritics, remove legal forms."""
    if not name:
        return ""

    result = name.strip()
    # Remove content in parentheses that looks like country info e.g. "(France)"
    # but keep it if it's part of the actual name
    result = result.lower()

    # Remove legal forms
    legal_forms = _load_legal_forms()
    for form in legal_forms:
        # Match at word boundaries with optional punctuation
        pattern = r'(?:^|[\s,])\s*' + re.escape(form) + r'\s*(?:[,.]?\s*$|(?=[\s,]))'
        result = re.sub(pattern, ' ', result, flags=re.IGNORECASE)

    # Remove diacritics
    result = unidecode(result)

    # Normalize whitespace and punctuation
    result = re.sub(r'[,.:;]+', ' ', result)
    result = re.sub(r'\s+', ' ', result)
    result = result.strip()

    return result


def normalize_address_part(text: Optional[str]) -> str:
    """Normalize a single address component for comparison."""
    if not text:
        return ""
    result = text.strip().lower()
    result = unidecode(result)
    # Normalize common abbreviations
    result = re.sub(r'\bstr\.?\b', 'street', result)
    result = re.sub(r'\bave\.?\b', 'avenue', result)
    result = re.sub(r'\brd\.?\b', 'road', result)
    result = re.sub(r'\bblvd\.?\b', 'boulevard', result)
    result = re.sub(r'\bdr\.?\b', 'drive', result)
    result = re.sub(r'\bst\.?\b', 'street', result)
    # Normalize whitespace and punctuation
    result = re.sub(r'[,.:;/]+', ' ', result)
    result = re.sub(r'\s+', ' ', result)
    return result.strip()


def extract_zip(zip_code: Optional[str]) -> str:
    """Extract numeric/core part of a ZIP code, stripping state prefixes like 'NY', 'CA'."""
    if not zip_code:
        return ""
    # Remove common state prefixes (US-style: "NY 10019", "CA 92130", "CT 06830")
    cleaned = re.sub(r'^[A-Z]{2}[\s-]*', '', zip_code.strip())
    # Remove FL- style prefixes
    cleaned = re.sub(r'^[A-Z]{2,3}-', '', cleaned)
    # Remove spaces
    cleaned = cleaned.replace(' ', '')
    return cleaned
