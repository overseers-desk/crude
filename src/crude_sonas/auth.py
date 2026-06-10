"""Sonas login over DDP — a customised Meteor accounts-password flow.

Sonas renames the password field to ``fpPassword`` (same ``{digest, algorithm}``
value) and requires a non-empty ``fpIds`` device fingerprint. A fingerprint the
server has not seen triggers an email device-verification step; once the emailed
link is opened the fingerprint is trusted and subsequent logins succeed. See
``docs/sonas.md`` for the full protocol and the one-time setup.
"""

from __future__ import annotations

from crude_sonas.client import ddp_call


def sonas_login(conn, user: str, digest: str, fingerprint: str) -> dict:
    """Full login with the SHA-256 password digest and the device fingerprint.

    Raises a RuntimeError with actionable guidance when the server demands device
    verification or rejects the fingerprint.
    """
    userfield = {"email": user} if "@" in user else {"username": user}
    try:
        return ddp_call(conn, "login", [{
            "user": userfield,
            "fpPassword": {"digest": digest, "algorithm": "sha-256"},
            "fpIds": [fingerprint],
        }])
    except RuntimeError as e:
        text = str(e).lower()
        if "verification" in text or "verify this" in text or "sent you an email" in text:
            raise RuntimeError(
                "Sonas sent a device-verification email for this login. Open the "
                "link in it once (it trusts this machine's fingerprint), then re-run. "
                "One-time setup; see docs/sonas.md.")
        if "fingerprint" in text:
            raise RuntimeError(
                "Login rejected: set a non-empty [sonas] fingerprint in config.toml. "
                "See docs/sonas.md.")
        raise


def sonas_resume(conn, token: str):
    """Resume a session from a cached Meteor token; None if the token is stale."""
    if not token:
        return None
    try:
        return ddp_call(conn, "login", [{"resume": token}])
    except Exception:
        return None
