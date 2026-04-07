"""OpenFIGI API client for ISIN-to-entity-name resolution."""

import logging
from typing import Optional

import httpx

from .cache import cache

logger = logging.getLogger(__name__)

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_TIMEOUT = 15.0


async def resolve_isin_to_name(isin: str) -> Optional[str]:
    """Resolve an ISIN code to a company/fund name via OpenFIGI.

    Returns the primary entity name if found, None otherwise.
    Results are cached to avoid redundant API calls.
    """
    names = await resolve_isin_to_names(isin)
    return names[0] if names else None


async def resolve_isin_to_names(isin: str) -> list[str]:
    """Resolve an ISIN code to ALL possible entity names via OpenFIGI.

    Returns a list of unique names from all OpenFIGI entries for this ISIN.
    The first name is the primary result; additional names are alternatives
    from different exchanges or share classes.
    """
    cached = cache.get("openfigi", {"isin": isin})
    if cached is not None:
        name = cached.get("name")
        names = cached.get("names", [])
        if names:
            return names
        return [name] if name else []

    try:
        async with httpx.AsyncClient(timeout=OPENFIGI_TIMEOUT) as client:
            resp = await client.post(
                OPENFIGI_URL,
                json=[{"idType": "ID_ISIN", "idValue": isin}],
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                logger.debug("OpenFIGI returned %d for ISIN %s", resp.status_code, isin)
                cache.set("openfigi", {"isin": isin}, {"name": None, "names": []})
                return []

            data = resp.json()
            if not data or not isinstance(data, list):
                cache.set("openfigi", {"isin": isin}, {"name": None, "names": []})
                return []

            entries = data[0].get("data", [])
            names: list[str] = []
            seen: set[str] = set()
            for entry in entries:
                name = entry.get("name", "").strip()
                if name and name.lower() not in seen:
                    names.append(name)
                    seen.add(name.lower())

            if names:
                logger.info("OpenFIGI resolved ISIN %s -> %s (%d names total)", isin, names[0], len(names))
                cache.set("openfigi", {"isin": isin}, {"name": names[0], "names": names})
                return names

    except (httpx.TransportError, httpx.TimeoutException, Exception) as e:
        logger.debug("OpenFIGI request failed for ISIN %s: %s", isin, e)

    cache.set("openfigi", {"isin": isin}, {"name": None, "names": []})
    return []
