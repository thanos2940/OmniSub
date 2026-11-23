"""Single-user authentication: password hashing and API-key generation.

Omnisub uses Sonarr-style auth — one username/password pair set by the operator,
plus one long-lived API key the frontend (and any script) sends as ``X-Api-Key``.
No session/cookie state, no user table. See docs/PLAN_auth_security.md.
"""
import hashlib
import hmac
import os
import secrets

PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    """Hash a password for storage. Format: pbkdf2$<iterations>$<salt-hex>$<hash-hex>."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification against a hash produced by hash_password()."""
    try:
        algo, iterations_str, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations_str)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def generate_api_key() -> str:
    """Generate a new long-lived API key (256 bits, hex-encoded)."""
    return secrets.token_hex(32)


def generate_webhook_secret() -> str:
    """Generate a new webhook token for Sonarr/Radarr callback URLs."""
    return secrets.token_hex(24)


# ------------------------------------------------------------------ who may call the API

# Login is always required (Sonarr v4 style). The one way around it is the API key; with
# ``auth_local_bypass`` on, devices that connect directly from this machine or the local network do not need it
# either ("not for local addresses"). A request that came through a reverse proxy never counts as local: behind
# Caddy on the same machine every visitor would otherwise look like 127.0.0.1.
import ipaddress  # noqa: E402

LOCAL_NETWORKS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "fc00::/7", "fe80::/10")]
FORWARDING_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host")


def credentials_set(config: dict) -> bool:
    """A username and password exist (until then only creating them is possible)."""
    return bool((config or {}).get("auth_username") and (config or {}).get("auth_password_hash"))


def is_local_request(client_host: str, headers) -> bool:
    """A direct connection from this machine or a private network address (no proxy in between)."""
    if any(h in headers for h in FORWARDING_HEADERS):
        return False
    try:
        ip = ipaddress.ip_address((client_host or "").split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return any(ip in net for net in LOCAL_NETWORKS)


def request_authorized(config: dict, headers, client_host: str, query_key: str = "") -> bool:
    """Whether a request may use the API: the right ``X-Api-Key`` (or ``apikey`` query parameter, for the event
    WebSocket a browser cannot add headers to), or a local address while "not for local addresses" is on."""
    config = config or {}
    stored = config.get("api_key") or ""
    supplied = headers.get("x-api-key") or query_key or ""
    if stored and supplied and hmac.compare_digest(str(supplied), str(stored)):
        return True
    return bool(config.get("auth_local_bypass")) and credentials_set(config) and is_local_request(client_host, headers)
