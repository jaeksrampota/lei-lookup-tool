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

    Returns the entity name if found, None otherwise.
    Results are cached to avoid redundant API calls.
    """
    cached = cache.get("openfigi", {"isin": isin})
    if cached is not None:
        return cached.get("name")

    try:
        async with httpx.AsyncClient(timeout=OPENFIGI_TIMEOUT) as client:
            resp = await client.post(
                OPENFIGI_URL,
                json=[{"idType": "ID_ISIN", "idValue": isin}],
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                logger.debug("OpenFIGI returned %d for ISIN %s", resp.status_code, isin)
                cache.set("openfigi", {"isin": isin}, {"name": None})
                return None

            data = resp.json()
            if not data or not isinstance(data, list):
                cache.set("openfigi", {"isin": isin}, {"name": None})
                return None

            entries = data[0].get("data", [])
            if entries:
                name = entries[0].get("name", "").strip()
                if name:
                    logger.info("OpenFIGI resolved ISIN %s -> %s", isin, name)
                    cache.set("openfigi", {"isin": isin}, {"name": name})
                    return name

    except (httpx.TransportError, httpx.TimeoutException, Exception) as e:
        logger.debug("OpenFIGI request failed for ISIN %s: %s", isin, e)

    cache.set("openfigi", {"isin": isin}, {"name": None})
    return None
