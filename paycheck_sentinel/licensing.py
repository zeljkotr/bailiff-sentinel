"""Offline Ed25519 license validation and persistent PostgreSQL trial state."""

import base64
import binascii
import json
import os
import uuid
from datetime import date

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import db


TRIAL_DAYS = 15
LICENSE_ENV = "PAYCHECK_SENTINEL_LICENSE"

# Public key only. The corresponding private key stays in license-tools.
PUBLIC_KEY_B64 = "WRSBNr2KDA5m/Ly255aZMENHFoBPpw4in83tCc6lCXY="


def _decode_urlsafe(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Missing encoded value")
    raw = value.encode("ascii")
    raw += b"=" * (-len(raw) % 4)
    return base64.b64decode(raw, altchars=b"-_", validate=True)


def verify_license(token, public_key_b64=PUBLIC_KEY_B64, today=None):
    """Return a validation result without raising for malformed license data."""
    today = today or date.today()

    try:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "PS1":
            raise ValueError("Invalid license format")

        payload_bytes = _decode_urlsafe(parts[1])
        signature = _decode_urlsafe(parts[2])
        public_key_bytes = base64.b64decode(public_key_b64, validate=True)

        if len(public_key_bytes) != 32:
            raise ValueError("Invalid public key")

        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(signature, payload_bytes)

        payload = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError("Unsupported license version")

        license_id = payload.get("id")
        uuid.UUID(str(license_id))

        licensee = payload.get("licensee")
        if not isinstance(licensee, str) or not licensee.strip():
            raise ValueError("Missing licensee")

        issued = date.fromisoformat(payload["issued"])
        expires_raw = payload.get("expires")
        expires = date.fromisoformat(expires_raw) if expires_raw is not None else None

        if issued > today:
            raise ValueError("License issue date is in the future")
        if expires is not None and expires < today:
            raise ValueError("License has expired")

        return {
            "valid": True,
            "license_id": str(license_id),
            "licensee": licensee.strip(),
            "expires": expires.isoformat() if expires else None,
            "reason": None,
        }

    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error,
            InvalidSignature, json.JSONDecodeError) as exc:
        return {"valid": False, "reason": str(exc) or "Invalid license"}
    except Exception:
        return {"valid": False, "reason": "Invalid license"}


def initialize_trial_state():
    """Create one persistent trial-start date shared by all workers/instances."""
    with db.get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_license_state (
                singleton_id SMALLINT PRIMARY KEY CHECK (singleton_id = 1),
                trial_started_at DATE NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO app_license_state (singleton_id, trial_started_at)
            VALUES (1, CURRENT_DATE)
            ON CONFLICT (singleton_id) DO NOTHING
            """
        )
        row = conn.execute(
            "SELECT trial_started_at FROM app_license_state WHERE singleton_id = 1"
        ).fetchone()

    if not row:
        raise RuntimeError("Could not initialize persistent trial state")

    return row["trial_started_at"]


def get_license_status(today=None):
    """Check the configured commercial license or the persistent trial."""
    today = today or date.today()
    token = os.environ.get(LICENSE_ENV, "").strip()

    if token:
        result = verify_license(token, today=today)
        if result["valid"]:
            return {
                "active": True,
                "type": "commercial",
                "licensee": result["licensee"],
                "expires": result["expires"],
                "reason": None,
            }
        return {
            "active": False,
            "type": "commercial",
            "reason": result["reason"],
        }

    with db.get_db() as conn:
        row = conn.execute(
            "SELECT trial_started_at FROM app_license_state WHERE singleton_id = 1"
        ).fetchone()

    if not row:
        return {
            "active": False,
            "type": "trial",
            "reason": "Trial state is not initialized",
        }

    started = row["trial_started_at"]
    if isinstance(started, str):
        started = date.fromisoformat(started)

    elapsed = (today - started).days

    if elapsed < 0:
        return {
            "active": False,
            "type": "trial",
            "reason": "System date is earlier than the recorded trial start",
        }

    remaining = max(0, TRIAL_DAYS - elapsed)
    active = elapsed < TRIAL_DAYS

    return {
        "active": active,
        "type": "trial",
        "trial_started_at": started.isoformat(),
        "days_remaining": remaining,
        "reason": None if active else "Trial period has expired",
    }
