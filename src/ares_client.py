"""ARES API client for Czech entity cross-verification."""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ARES_API_BASE = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/vyhledat"


async def search_ares(name: str) -> Optional[dict]:
    """Search ARES for a Czech entity by name.

    Returns basic entity info if found, None otherwise.
    This is a secondary/optional verification source.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                ARES_API_BASE,
                params={"obchodniJmeno": name, "start": 0, "pocet": 5},
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("ekonomickeSubjekty", [])
                if items:
                    return items[0]
    except Exception as e:
        logger.warning("ARES lookup failed for %s: %s", name, e)

    return None
