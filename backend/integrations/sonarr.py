"""
Sonarr API Client — v3 REST API integration.

Queries series and episode metadata for use as the media source of truth.
Omnisub reads what Sonarr knows about the library,
then scans the filesystem directly for subtitles.

Sonarr API reference (v3):
    GET /api/v3/series             — all series
    GET /api/v3/episode?seriesId=X — episodes for a series
    GET /api/v3/system/status      — health / version check
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None

HTTPX_LIMITS = httpx.Limits(
    max_keepalive_connections=10,
    max_connections=20,
    keepalive_expiry=30.0,
)

RETRYABLE_EXCEPTIONS = (httpx.TransportError, ConnectionResetError)


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(limits=HTTPX_LIMITS, timeout=30.0)
    return _client


async def close_client():
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        finally:
            _client = None


async def _get_with_retry(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict] = None,
    timeout: float = 30.0,
    max_retries: int = 2,
) -> httpx.Response:
    """Execute GET request with automatic reconnect and retry upon stale socket drop."""
    for attempt in range(max_retries + 1):
        client = get_client()
        try:
            return await client.get(url, headers=headers, params=params, timeout=timeout)
        except RETRYABLE_EXCEPTIONS as exc:
            logger.warning(
                f"Transport/socket error requesting {url} (attempt {attempt + 1}/{max_retries + 1}): {exc}. "
                "Resetting client connection pool..."
            )
            await close_client()
            if attempt < max_retries:
                await asyncio.sleep(0.5 * (attempt + 1))
            else:
                raise


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
        resp = await _get_with_retry(
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
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/series",
            headers=config.headers,
            timeout=30.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Sonarr returned 401 Unauthorized for {config.normalized_url}. Please check your Sonarr API Key in Settings.")
        if resp.status_code != 200:
            logger.error(f"Sonarr /api/v3/series returned {resp.status_code}")
            resp.raise_for_status()
        return resp.json() or []
    except PermissionError:
        raise
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
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/episode",
            headers=config.headers,
            params={"seriesId": series_id},
            timeout=30.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Sonarr returned 401 Unauthorized for {config.normalized_url}. Please check your Sonarr API Key in Settings.")
        if resp.status_code != 200:
            logger.error(f"Sonarr /api/v3/episode returned {resp.status_code}")
            return []
        return resp.json() or []
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch episodes for series {series_id}: {e}")
        return []


async def get_episode_file(config: SonarrConfig, episode_file_id: int) -> Optional[Dict]:
    """Fetch episode file metadata (includes the media file path)."""
    try:
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/episodefile/{episode_file_id}",
            headers=config.headers,
            timeout=10.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Sonarr returned 401 Unauthorized for {config.normalized_url}. Please check your Sonarr API Key in Settings.")
        if resp.status_code == 200:
            return resp.json()
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch episode file {episode_file_id}: {e}")
    return None


async def get_series(config: SonarrConfig, series_id: int) -> Optional[Dict]:
    """Fetch a single series by Sonarr ID."""
    try:
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/series/{series_id}",
            headers=config.headers,
            timeout=10.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Sonarr returned 401 Unauthorized for {config.normalized_url}. Please check your Sonarr API Key in Settings.")
        if resp.status_code == 200:
            return resp.json()
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch Sonarr series {series_id}: {e}")
    return None


async def find_series_by_tvdb(config: SonarrConfig, tvdb_id: int) -> Optional[Dict]:
    """Fetch the library series with this TVDB id, or None if Sonarr does not have it.

    Sonarr filters /series server-side on tvdbId, so this avoids pulling the whole
    library just to resolve one on-demand request.
    """
    try:
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/series",
            headers=config.headers,
            params={"tvdbId": tvdb_id},
            timeout=15.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Sonarr returned 401 Unauthorized for {config.normalized_url}. Please check your Sonarr API Key in Settings.")
        if resp.status_code == 200:
            results = resp.json() or []
            for series in results:
                if str(series.get("tvdbId")) == str(tvdb_id):
                    return series
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to look up Sonarr series by tvdbId {tvdb_id}: {e}")
    return None


async def get_episode_files(config: SonarrConfig, series_id: int) -> Optional[List[Dict]]:
    """Fetch all episode files for a specific series in one API call.
    
    Returns None if an API error or exception occurs so callers can distinguish
    between a series with genuine 0 files and an API failure.
    """
    try:
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/episodefile",
            headers=config.headers,
            params={"seriesId": series_id},
            timeout=30.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Sonarr returned 401 Unauthorized for {config.normalized_url}. Please check your Sonarr API Key in Settings.")
        if resp.status_code == 200:
            return resp.json() or []
        logger.error(f"Sonarr /api/v3/episodefile returned {resp.status_code}")
        return None
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch episode files for series {series_id}: {e}")
        return None





async def get_history_since(config: SonarrConfig, since_iso: str, event_type: str) -> Optional[List[Dict]]:
    """History records of one event type since ``since_iso`` (series and episode included).

    Returns None when the call failed, [] when nothing is new, so a poller never
    advances its cursor on an outage.
    """
    try:
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/history/since",
            headers=config.headers,
            params={
                "date": since_iso,
                "eventType": event_type,
                "includeSeries": "true",
                "includeEpisode": "true",
            },
            timeout=30.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Sonarr returned 401 Unauthorized for {config.normalized_url}. Please check your Sonarr API Key in Settings.")
        if resp.status_code == 200:
            return resp.json() or []
        logger.error(f"Sonarr /api/v3/history/since returned {resp.status_code}")
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch Sonarr history since {since_iso} ({event_type}): {e}")
    return None


async def get_episode(config: SonarrConfig, episode_id: int) -> Optional[Dict]:
    """Fetch a single episode by Sonarr episode ID."""
    try:
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/episode/{episode_id}",
            headers=config.headers,
            timeout=10.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Sonarr returned 401 Unauthorized for {config.normalized_url}. Please check your Sonarr API Key in Settings.")
        if resp.status_code == 200:
            return resp.json()
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch Sonarr episode {episode_id}: {e}")
    return None


async def get_tags(config: SonarrConfig) -> Optional[List[Dict]]:
    """All tags (id, label), or None when the call failed."""
    try:
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/tag",
            headers=config.headers,
            timeout=10.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Sonarr returned 401 Unauthorized for {config.normalized_url}. Please check your Sonarr API Key in Settings.")
        if resp.status_code == 200:
            return resp.json() or []
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch Sonarr tags: {e}")
    return None
