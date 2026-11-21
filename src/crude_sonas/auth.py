"""Sonas login over DDP — a customised Meteor accounts-password flow.

Sonas renames the password field to ``fpPassword`` (same ``{digest, algorithm}``
value) and requires a non-empty ``fpIds`` device fingerprint. A password login
from a fingerprint and location the server does not trust is refused with a
``verification-error`` and a link emailed to the account; once the link is
opened, password logins from there succeed. A resume is not checked this way.
See ``docs/sonas.md`` §3 for what was observed and the one-time setup.
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
        detail = e.args[0] if e.args and isinstance(e.args[0], dict) else {}
        text = str(e).lower()
        if detail.get("error") == "verification-error" or "verification" in text \
                or "verify this" in text or "sent you an email" in text:
            # Sonas's own sentence says whether it sent, re-sent or held back the
            # email and how long the link lasts, so it is passed on, not paraphrased.
            said = (detail.get("reason") or "").strip()
            raise RuntimeError(
                "Sonas refused this login until it is verified by email"
                + (f' ("{said}")' if said else "") + ". Open the newest link sent "
                "to the account's address once, then re-run; later logins from this "
                "machine and network then pass. See docs/sonas.md §3.")
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
