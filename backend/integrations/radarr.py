"""
Radarr API Client — v3 REST API integration.

Queries movie metadata for use as the media source of truth.
Omnisub reads what Radarr knows about the movie library, then scans
the filesystem directly for subtitles.

Radarr API reference (v3):
    GET /api/v3/movie              — all movies
    GET /api/v3/system/status      — health / version check
"""

import logging
from dataclasses import dataclass
from typing import List, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


async def close_client():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


@dataclass
class RadarrConfig:
    """Connection settings for a Radarr instance."""
    base_url: str = "http://localhost:7878"
    api_key: str = ""
    enabled: bool = False

    @property
    def normalized_url(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def headers(self) -> Dict[str, str]:
        return {"X-Api-Key": self.api_key}


async def test_connection(config: RadarrConfig) -> Dict:
    """Test Radarr reachability and API key validity.

    Returns:
        {connected: bool, version: str, error: str|None}
    """
    try:
        client = get_client()
        resp = await client.get(
            f"{config.normalized_url}/api/v3/system/status",
            headers=config.headers,
            timeout=10.0,
        )
        if resp.status_code == 401:
            return {"connected": False, "error": "Invalid API key"}
        if resp.status_code == 200:
            data = resp.json()
            return {
                "connected": True,
                "version": data.get("version", "unknown"),
                "error": None,
            }
        return {"connected": False, "error": f"HTTP {resp.status_code}"}
    except httpx.ConnectError:
        return {"connected": False, "error": f"Cannot connect to {config.base_url}"}
    except Exception as e:
        return {"connected": False, "error": str(e)}


async def get_all_movies(config: RadarrConfig) -> List[Dict]:
    """Fetch all movies from Radarr with full metadata.

    Each dict contains at minimum:
        id, title, year, folderPath, movieFile (if downloaded)
    """
    try:
        client = get_client()
        resp = await client.get(
            f"{config.normalized_url}/api/v3/movie",
            headers=config.headers,
            timeout=30.0,
        )
        if resp.status_code != 200:
            logger.error(f"Radarr /api/v3/movie returned {resp.status_code}")
            resp.raise_for_status()
        return resp.json() or []
    except Exception as e:
        logger.error(f"Failed to fetch Radarr movies: {e}")
        raise


async def get_movie(config: RadarrConfig, movie_id: int) -> Optional[Dict]:
    """Fetch a single movie by Radarr ID."""
    try:
        client = get_client()
        resp = await client.get(
            f"{config.normalized_url}/api/v3/movie/{movie_id}",
            headers=config.headers,
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch Radarr movie {movie_id}: {e}")
    return None
