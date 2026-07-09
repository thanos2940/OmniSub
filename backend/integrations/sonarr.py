"""
Sonarr API Client — v3 REST API integration.

Queries series and episode metadata for use as the media source of truth
instead of Bazarr. Omnisub reads what Sonarr knows about the library,
then scans the filesystem directly for subtitles.

Sonarr API reference (v3):
    GET /api/v3/series             — all series
    GET /api/v3/episode?seriesId=X — episodes for a series
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
class SonarrConfig:
    """Connection settings for a Sonarr instance."""
    base_url: str = "http://localhost:8989"
    api_key: str = ""
    enabled: bool = False

    @property
    def normalized_url(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def headers(self) -> Dict[str, str]:
        return {"X-Api-Key": self.api_key}


async def test_connection(config: SonarrConfig) -> Dict:
    """Test Sonarr reachability and API key validity.

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


async def get_all_series(config: SonarrConfig) -> List[Dict]:
    """Fetch all series from Sonarr with full metadata.

    Each dict contains at minimum:
        id, title, path, seriesType, seasons, statistics
    """
    try:
        client = get_client()
        resp = await client.get(
            f"{config.normalized_url}/api/v3/series",
            headers=config.headers,
            timeout=30.0,
        )
        if resp.status_code != 200:
            logger.error(f"Sonarr /api/v3/series returned {resp.status_code}")
            resp.raise_for_status()
        return resp.json() or []
    except Exception as e:
        logger.error(f"Failed to fetch Sonarr series: {e}")
        raise


async def get_episodes(config: SonarrConfig, series_id: int) -> List[Dict]:
    """Fetch all episodes for a specific series.

    Each dict contains at minimum:
        id, seriesId, seasonNumber, episodeNumber, title,
        hasFile, episodeFileId
    """
    try:
        client = get_client()
        resp = await client.get(
            f"{config.normalized_url}/api/v3/episode",
            headers=config.headers,
            params={"seriesId": series_id},
            timeout=30.0,
        )
        if resp.status_code != 200:
            logger.error(f"Sonarr /api/v3/episode returned {resp.status_code}")
            return []
        return resp.json() or []
    except Exception as e:
        logger.error(f"Failed to fetch episodes for series {series_id}: {e}")
        return []


async def get_episode_file(config: SonarrConfig, episode_file_id: int) -> Optional[Dict]:
    """Fetch episode file metadata (includes the media file path)."""
    try:
        client = get_client()
        resp = await client.get(
            f"{config.normalized_url}/api/v3/episodefile/{episode_file_id}",
            headers=config.headers,
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch episode file {episode_file_id}: {e}")
    return None


async def get_series(config: SonarrConfig, series_id: int) -> Optional[Dict]:
    """Fetch a single series by Sonarr ID."""
    try:
        client = get_client()
        resp = await client.get(
            f"{config.normalized_url}/api/v3/series/{series_id}",
            headers=config.headers,
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch Sonarr series {series_id}: {e}")
    return None


async def get_episode_files(config: SonarrConfig, series_id: int) -> List[Dict]:
    """Fetch all episode files for a specific series in one API call."""
    try:
        client = get_client()
        resp = await client.get(
            f"{config.normalized_url}/api/v3/episodefile",
            headers=config.headers,
            params={"seriesId": series_id},
            timeout=30.0,
        )
        if resp.status_code == 200:
            return resp.json() or []
        logger.error(f"Sonarr /api/v3/episodefile returned {resp.status_code}")
    except Exception as e:
        logger.error(f"Failed to fetch episode files for series {series_id}: {e}")
    return []


