"""Data models for the LEI Lookup Tool."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MatchType(str, Enum):
    FULL_MATCH = "FULL_MATCH"
    HQ_MATCH = "HQ_MATCH"
    ISIN_MATCH = "ISIN_MATCH"
    ISIN_GLEIF_MATCH = "ISIN_GLEIF_MATCH"
    NO_MATCH = "NO_MATCH"


class InputEntity(BaseModel):
    """Entity from the input Excel file."""

    name: str = Field(..., max_length=500)
    isin: Optional[str] = Field(None, max_length=20)
    street: Optional[str] = Field(None, max_length=500)
    town: Optional[str] = Field(None, max_length=200)
    country: Optional[str] = Field(None, max_length=200)  # Czech name, needs conversion to ISO
    zip_code: Optional[str] = Field(None, max_length=20)


class GleifAddress(BaseModel):
    """Address as returned by GLEIF API."""

    country: Optional[str] = None  # ISO 3166-1 alpha-2
    region: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    address_lines: list[str] = Field(default_factory=list)

    def format(self) -> str:
        parts = []
        if self.address_lines:
            parts.append(", ".join(self.address_lines))
        if self.city:
            parts.append(self.city)
        if self.postal_code:
            parts.append(self.postal_code)
        if self.country:
            parts.append(self.country)
        return ", ".join(parts)


class GleifCandidate(BaseModel):
    """A candidate LEI record from GLEIF search."""

    lei: str
    legal_name: str
    status: str  # ISSUED, LAPSED, RETIRED, etc.
    legal_address: Optional[GleifAddress] = None
    hq_address: Optional[GleifAddress] = None
    other_names: list[str] = Field(default_factory=list)


class LookupResult(BaseModel):
    """Result of a single LEI lookup."""

    lei: Optional[str] = None
    lei_status: Optional[str] = None
    match_type: MatchType = MatchType.NO_MATCH
    confidence: float = 0.0
    gleif_legal_name: Optional[str] = None
    gleif_legal_address: Optional[str] = None
    gleif_hq_address: Optional[str] = None
    notes: str = ""
