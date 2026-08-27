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
