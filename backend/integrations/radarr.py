"""
Radarr API Client — v3 REST API integration.

Queries movie metadata for use as the media source of truth.
Omnisub reads what Radarr knows about the movie library, then scans
the filesystem directly for subtitles.

Radarr API reference (v3):
    GET /api/v3/movie              — all movies
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


async def get_all_movies(config: RadarrConfig) -> List[Dict]:
    """Fetch all movies from Radarr with full metadata.

    Each dict contains at minimum:
        id, title, year, folderPath, movieFile (if downloaded)
    """
    try:
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/movie",
            headers=config.headers,
            timeout=30.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Radarr returned 401 Unauthorized for {config.normalized_url}. Please check your Radarr API Key in Settings.")
        if resp.status_code != 200:
            logger.error(f"Radarr /api/v3/movie returned {resp.status_code}")
            resp.raise_for_status()
        return resp.json() or []
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch Radarr movies: {e}")
        raise


async def find_movie_by_tmdb(config: RadarrConfig, tmdb_id: int) -> Optional[Dict]:
    """Fetch the library movie with this TMDB id, or None if Radarr does not have it.

    Radarr filters /movie server-side on tmdbId, so this avoids pulling the whole
    library just to resolve one on-demand request.
    """
    try:
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/movie",
            headers=config.headers,
            params={"tmdbId": tmdb_id},
            timeout=15.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Radarr returned 401 Unauthorized for {config.normalized_url}. Please check your Radarr API Key in Settings.")
        if resp.status_code == 200:
            results = resp.json() or []
            for movie in results:
                if str(movie.get("tmdbId")) == str(tmdb_id):
                    return movie
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to look up Radarr movie by tmdbId {tmdb_id}: {e}")
    return None


async def get_movie(config: RadarrConfig, movie_id: int) -> Optional[Dict]:
    """Fetch a single movie by Radarr ID."""
    try:
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/movie/{movie_id}",
            headers=config.headers,
            timeout=10.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Radarr returned 401 Unauthorized for {config.normalized_url}. Please check your Radarr API Key in Settings.")
        if resp.status_code == 200:
            return resp.json()
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch Radarr movie {movie_id}: {e}")
    return None


async def get_history_since(config: RadarrConfig, since_iso: str, event_type: str) -> Optional[List[Dict]]:
    """History records of one event type since ``since_iso`` (movie included).

    Returns None when the call failed, [] when nothing is new, so a poller never
    advances its cursor on an outage.
    """
    try:
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/history/since",
            headers=config.headers,
            params={"date": since_iso, "eventType": event_type, "includeMovie": "true"},
            timeout=30.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Radarr returned 401 Unauthorized for {config.normalized_url}. Please check your Radarr API Key in Settings.")
        if resp.status_code == 200:
            return resp.json() or []
        logger.error(f"Radarr /api/v3/history/since returned {resp.status_code}")
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch Radarr history since {since_iso} ({event_type}): {e}")
    return None


async def get_movie_file(config: RadarrConfig, movie_file_id: int) -> Optional[Dict]:
    """Fetch movie file metadata (includes the media file path)."""
    try:
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/moviefile/{movie_file_id}",
            headers=config.headers,
            timeout=10.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Radarr returned 401 Unauthorized for {config.normalized_url}. Please check your Radarr API Key in Settings.")
        if resp.status_code == 200:
            return resp.json()
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch Radarr movie file {movie_file_id}: {e}")
    return None


async def get_tags(config: RadarrConfig) -> Optional[List[Dict]]:
    """All tags (id, label), or None when the call failed."""
    try:
        resp = await _get_with_retry(
            f"{config.normalized_url}/api/v3/tag",
            headers=config.headers,
            timeout=10.0,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Radarr returned 401 Unauthorized for {config.normalized_url}. Please check your Radarr API Key in Settings.")
        if resp.status_code == 200:
            return resp.json() or []
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch Radarr tags: {e}")
    return None
