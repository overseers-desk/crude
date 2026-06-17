"""Airwallex API-key login and durable bearer-token store.

Airwallex authenticates by exchanging client_id + api_key for a short-lived bearer:
POST /api/v1/authentication/login with x-client-id / x-api-key returns a ``token``
(~30 min) and an ``expires_at``. There is no OAuth consent, no redirect, and no
refresh token; re-authentication is just logging in again, which is idempotent. So
unlike crude-xero (whose single-use rotating refresh token needs an flock and a
never-clobber rule) this keeps only a durable side file caching the bearer to skip
a login round-trip on every call; losing it costs one re-login. Expiry is stored as
epoch seconds, never a naive datetime.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from crude_common.localtime import parse_iso_utc
from crude_common.statestore import atomic_write, state_path
from crude_airwallex.client import AirwallexAuthError

PROD_BASE = "https://api.airwallex.com"
DEMO_BASE = "https://api-demo.airwallex.com"
LOGIN_PATH = "/api/v1/authentication/login"

# API-key bearer tokens last ~30 minutes; used when the login response omits a
# parseable expires_at.
_ACCESS_TTL = 1800


def base_url(environment) -> str:
    """The production host by default; the demo/sandbox host for a demo environment."""
    if environment and str(environment).strip().lower() in ("demo", "sandbox"):
        return DEMO_BASE
    return PROD_BASE


def login(client_id, api_key, *, base) -> dict:
    """Exchange client_id + api_key for a bearer grant ``{"token", "expires_at"}``.

    POSTs the two credential headers with an empty body. Raises AirwallexAuthError
    on a non-2xx, surfacing the API's message.
    """
    r = requests.post(base + LOGIN_PATH, headers={
        "x-client-id": client_id,
        "x-api-key": api_key,
        "Accept": "application/json",
    })
    if not r.ok:
        try:
            body = r.json()
        except ValueError:
            body = {}
        detail = ""
        if isinstance(body, dict):
            detail = body.get("message") or body.get("error") or body.get("code") or ""
        raise AirwallexAuthError(
            f"Airwallex login failed: HTTP {r.status_code} {detail}".rstrip(),
            status=r.status_code,
        )
    return r.json() if r.content else {}


def _durable_token(grant: dict) -> dict:
    """Map a raw login grant to the durable side-file shape (epoch expiry).

    ``expires_at`` from the API is an ISO-8601 UTC instant, stored as epoch seconds.
    An absent or unparseable expiry falls back to now + ~30 min so the cache stays
    usable; a stale token then just triggers one re-login on the next 401.
    """
    dt = parse_iso_utc(grant.get("expires_at"))
    expires_at = dt.timestamp() if dt is not None else time.time() + _ACCESS_TTL
    return {"token": grant.get("token"), "expires_at": expires_at}


def token_store_path(account) -> Path:
    """The durable bearer-token side file, keyed by account, under $XDG_STATE_HOME.

    The default account keeps ``airwallex_token.json``; a named account uses
    ``airwallex_token_<account>.json``.
    """
    return state_path("airwallex_token.json" if not account else f"airwallex_token_{account}.json")


def load_token(account) -> "dict | None":
    """Return the cached bearer token for an account, or None if absent or corrupt."""
    path = token_store_path(account)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            return None
    return None


def save_token(account, token: dict) -> None:
    """Persist the bearer token atomically (mode 0600) to the durable side file."""
    atomic_write(token_store_path(account), json.dumps(token, indent=2))
