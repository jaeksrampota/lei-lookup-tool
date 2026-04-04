"""Address normalization and country code conversion."""

import json
import re
from pathlib import Path
from typing import Optional

from unidecode import unidecode

_COUNTRY_MAP: Optional[dict[str, str]] = None
_LEGAL_FORMS: Optional[list[str]] = None
_LEGAL_FORM_PATTERNS: Optional[list[re.Pattern]] = None

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Pre-compiled regex patterns for normalize_address_part
_RE_STREET_ABBR = re.compile(r'\bstr\.?\b', re.IGNORECASE)
_RE_AVE_ABBR = re.compile(r'\bave\.?\b', re.IGNORECASE)
_RE_RD_ABBR = re.compile(r'\brd\.?\b', re.IGNORECASE)
_RE_BLVD_ABBR = re.compile(r'\bblvd\.?\b', re.IGNORECASE)
_RE_DR_ABBR = re.compile(r'\bdr\.?\b', re.IGNORECASE)
_RE_ST_ABBR = re.compile(r'\bst\.?\b', re.IGNORECASE)
_RE_PUNCT = re.compile(r'[,.:;/]+')
_RE_WHITESPACE = re.compile(r'\s+')
# For normalize_name
_RE_COMMA_TRAIL = re.compile(r'[,.:;]+')
_RE_ZIP_STATE = re.compile(r'^[A-Z]{2}[\s-]*')
_RE_ZIP_PREFIX = re.compile(r'^[A-Z]{2,3}-')


def _load_country_map() -> dict[str, str]:
    global _COUNTRY_MAP
    if _COUNTRY_MAP is None:
        with open(DATA_DIR / "country_mapping.json", encoding="utf-8") as f:
            _COUNTRY_MAP = json.load(f)
    return _COUNTRY_MAP


def _load_legal_forms() -> list[str]:
    global _LEGAL_FORMS, _LEGAL_FORM_PATTERNS
    if _LEGAL_FORMS is None:
        with open(DATA_DIR / "legal_forms.txt", encoding="utf-8") as f:
            _LEGAL_FORMS = [
                line.strip().lower()
                for line in f
                if line.strip()
            ]
        # Sort longest first so we strip "pty ltd" before "ltd"
        _LEGAL_FORMS.sort(key=len, reverse=True)
        # Pre-compile patterns for each legal form
        _LEGAL_FORM_PATTERNS = [
            re.compile(r'(?:^|[\s,])\s*' + re.escape(form) + r'\s*(?:[,.]?\s*$|(?=[\s,]))', re.IGNORECASE)
            for form in _LEGAL_FORMS
        ]
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
    result = result.lower()

    # Remove legal forms using pre-compiled patterns
    _load_legal_forms()
    for pattern in _LEGAL_FORM_PATTERNS:
        result = pattern.sub(' ', result)

    # Remove diacritics
    result = unidecode(result)

    # Normalize whitespace and punctuation
    result = _RE_COMMA_TRAIL.sub(' ', result)
    result = _RE_WHITESPACE.sub(' ', result)
    result = result.strip()

    return result


def normalize_address_part(text: Optional[str]) -> str:
    """Normalize a single address component for comparison."""
    if not text:
        return ""
    result = text.strip().lower()
    result = unidecode(result)
    # Normalize common abbreviations (pre-compiled patterns)
    result = _RE_STREET_ABBR.sub('street', result)
    result = _RE_AVE_ABBR.sub('avenue', result)
    result = _RE_RD_ABBR.sub('road', result)
    result = _RE_BLVD_ABBR.sub('boulevard', result)
    result = _RE_DR_ABBR.sub('drive', result)
    result = _RE_ST_ABBR.sub('street', result)
    # Normalize whitespace and punctuation
    result = _RE_PUNCT.sub(' ', result)
    result = _RE_WHITESPACE.sub(' ', result)
    return result.strip()


def extract_zip(zip_code: Optional[str]) -> str:
    """Extract numeric/core part of a ZIP code, stripping state prefixes like 'NY', 'CA'."""
    if not zip_code:
        return ""
    # Remove common state prefixes (US-style: "NY 10019", "CA 92130", "CT 06830")
    cleaned = _RE_ZIP_STATE.sub('', zip_code.strip())
    # Remove FL- style prefixes
    cleaned = _RE_ZIP_PREFIX.sub('', cleaned)
    # Remove spaces
    cleaned = cleaned.replace(' ', '')
    return cleaned
