"""GLEIF API client for LEI lookups."""

import asyncio
import logging
from typing import Optional

import httpx

from .cache import cache
from .models import GleifAddress, GleifCandidate

logger = logging.getLogger(__name__)

GLEIF_API_BASE = "https://api.gleif.org/api/v1"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0


def _parse_address(data: dict) -> GleifAddress:
    """Parse a GLEIF address object."""
    return GleifAddress(
        country=data.get("country"),
        region=data.get("region"),
        city=data.get("city"),
        postal_code=data.get("postalCode"),
        address_lines=data.get("addressLines", []),
    )


def _parse_candidate(record: dict) -> GleifCandidate:
    """Parse a single GLEIF API record into a GleifCandidate."""
    attrs = record.get("attributes", {})
    entity = attrs.get("entity", {})

    legal_name = entity.get("legalName", {}).get("name", "")
    status = attrs.get("registration", {}).get("status", "UNKNOWN")

    legal_addr = entity.get("legalAddress", {})
    hq_addr = entity.get("headquartersAddress", {})

    other_names = []
    for on in entity.get("otherNames", []):
        if isinstance(on, dict):
            n = on.get("name", "")
            if n:
                other_names.append(n)
        elif isinstance(on, str):
            other_names.append(on)

    # Also parse transliteratedOtherNames (e.g. Chinese entities with ASCII names)
    for on in entity.get("transliteratedOtherNames", []):
        if isinstance(on, dict):
            n = on.get("name", "")
            if n:
                other_names.append(n)
        elif isinstance(on, str):
            other_names.append(on)

    return GleifCandidate(
        lei=record.get("id", attrs.get("lei", "")),
        legal_name=legal_name,
        status=status,
        legal_address=_parse_address(legal_addr) if legal_addr else None,
        hq_address=_parse_address(hq_addr) if hq_addr else None,
        other_names=other_names,
    )


class GleifClient:
    """Async client for the GLEIF API with retry and caching."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=GLEIF_API_BASE,
                timeout=self._timeout,
                headers={"Accept": "application/vnd.api+json"},
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request(self, path: str, params: dict) -> dict:
        """Make a GET request with retry and exponential backoff."""
        cached = cache.get("gleif", {"path": path, **params})
        if cached is not None:
            return cached

        client = await self._get_client()
        backoff = INITIAL_BACKOFF

        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.get(path, params=params)
                if resp.status_code == 429:
                    logger.warning("GLEIF rate limited, retrying in %.1fs", backoff)
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                resp.raise_for_status()
                data = resp.json()
                cache.set("gleif", {"path": path, **params}, data)
                return data
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                raise
            except httpx.TransportError:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                raise

        return {"data": []}

    async def search_by_name(
        self, name: str, country: Optional[str] = None, page_size: int = 10
    ) -> list[GleifCandidate]:
        """Search GLEIF by entity name with optional country filter.

        Uses multiple search strategies:
        1. Fulltext search with original name
        2. legalName filter with original name
        3. legalName filter with cleaned name (no legal forms, no abbreviations)
        """
        existing_leis: set[str] = set()
        candidates: list[GleifCandidate] = []

        def _add(records: list[dict]) -> None:
            for r in records:
                c = _parse_candidate(r)
                if c.lei not in existing_leis:
                    candidates.append(c)
                    existing_leis.add(c.lei)

        # Strategy 1: Fulltext search
        params: dict = {
            "filter[fulltext]": name,
            "page[size]": str(page_size),
        }
        if country:
            params["filter[entity.legalAddress.country]"] = country
        data = await self._request("/lei-records", params)
        _add(data.get("data", []))

        # Strategy 2: legalName filter with original name
        params2: dict = {
            "filter[entity.legalName]": name,
            "page[size]": str(page_size),
        }
        if country:
            params2["filter[entity.legalAddress.country]"] = country
        data2 = await self._request("/lei-records", params2)
        _add(data2.get("data", []))

        # Strategy 3: Try with cleaned name (expand abbreviations, remove diacritics)
        import re
        from unidecode import unidecode

        clean = name.strip()
        clean = re.sub(r'\bLmt\.?\b', 'Limited', clean, flags=re.IGNORECASE)
        clean = re.sub(r'\bCorp\.?\b', 'Corporation', clean, flags=re.IGNORECASE)
        clean = re.sub(r'\bInc\.?\b', 'Incorporated', clean, flags=re.IGNORECASE)
        clean = unidecode(clean)

        if clean != name:
            params3: dict = {
                "filter[entity.legalName]": clean,
                "page[size]": str(page_size),
            }
            if country:
                params3["filter[entity.legalAddress.country]"] = country
            data3 = await self._request("/lei-records", params3)
            _add(data3.get("data", []))

        # Strategy 4: Strip parenthetical info and legal forms, search via legalName
        stripped = re.sub(r'\(.*?\)', '', name).strip()
        stripped = re.sub(
            r'\b(S\.A\.S\.?|SAS|LLP|LLC|Ltd\.?|Inc\.?|Corp\.?|GmbH|AG|Lmt\.?|a\.s\.?|s\.r\.o\.?)\b',
            '', stripped, flags=re.IGNORECASE,
        )
        stripped = re.sub(r'[,;]+\s*$', '', stripped)
        stripped = re.sub(r'\s+', ' ', stripped).strip()
        stripped = unidecode(stripped)

        if stripped and stripped != unidecode(name) and len(stripped) > 3:
            # Try legalName filter (most precise)
            params4: dict = {
                "filter[entity.legalName]": stripped,
                "page[size]": str(page_size),
            }
            data4 = await self._request("/lei-records", params4)
            _add(data4.get("data", []))

            # Also try as fulltext with country
            if country:
                params5: dict = {
                    "filter[fulltext]": stripped,
                    "filter[entity.legalAddress.country]": country,
                    "page[size]": str(page_size),
                }
                data5 = await self._request("/lei-records", params5)
                _add(data5.get("data", []))

        return candidates

    async def search_by_name_no_country(
        self, name: str, page_size: int = 10
    ) -> list[GleifCandidate]:
        """Search without country filter as a fallback."""
        return await self.search_by_name(name, country=None, page_size=page_size)

    async def search_by_isin(self, isin: str) -> list[GleifCandidate]:
        """Search for LEI records associated with an ISIN code.

        GLEIF doesn't have a direct ISIN filter, but we can try fulltext search
        with the ISIN and look for related records.
        """
        params = {
            "filter[fulltext]": isin,
            "page[size]": "5",
        }
        data = await self._request("/lei-records", params)
        return [_parse_candidate(r) for r in data.get("data", [])]

    async def get_lei_record(self, lei: str) -> Optional[GleifCandidate]:
        """Fetch a specific LEI record."""
        try:
            data = await self._request(f"/lei-records/{lei}", {})
            record = data.get("data")
            if record:
                return _parse_candidate(record)
        except httpx.HTTPStatusError:
            pass
        return None
