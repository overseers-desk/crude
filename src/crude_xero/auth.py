"""Xero OAuth2 flow and durable token store.

Two Xero app types are supported, selected by whether config sets a
client_secret. Without one, crude-xero is a public client (a command-line tool
holds no server secret) and authorizes with PKCE: each consent mints a one-shot
code_verifier and sends its S256 code_challenge to the authorize endpoint, and
the token endpoint is called with the client_id and verifier. With one, the
consent runs the confidential web-app flow, the token endpoint authenticated
over HTTP Basic: this is how crude rides a Web-app registration's existing
organisation connection, which Xero's cap on uncertified-app connections can
make the only seat available (see docs/xero.md). The refresh token rotates on
every refresh (single-use) and dies after 60 days idle, and there is no password
to silently re-login, so the rotating token is persisted to a durable,
account-keyed JSON side file under the XDG state dir, written atomically under an
flock, and left intact on a failed refresh. Expiry is stored as epoch seconds, never a
naive datetime.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import requests

from crude_common.oauth_consent import (  # noqa: F401  (re-exported for callers and tests)
    generate_pkce_verifier,
    generate_state,
    loopback_code,
    manual_code,
    pkce_challenge,
)
from crude_common.statestore import state_path, write_or_exit
from crude_xero.client import XeroAuthError

AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"

# Access tokens last 30 minutes; used when seeding from a parseable timestamp.
_ACCESS_TTL = 1800


def _scope_str(scopes) -> str:
    """Coerce scopes (list or pre-joined string) to a space-separated string."""
    return scopes if isinstance(scopes, str) else " ".join(scopes)


def build_authorize_url(client_id, redirect_uri, scopes, state, code_challenge=None) -> str:
    """The Xero consent URL: response_type=code, plus the S256 challenge when PKCE.

    A confidential web-app consent (config sets client_secret) sends no
    challenge; the token exchange authenticates with the secret instead.
    """
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": _scope_str(scopes),
        "state": state,
    }
    if code_challenge is not None:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


# ----------------------------------------------------------------------
# Token endpoint
# ----------------------------------------------------------------------


def _token_request(data, client_id=None, client_secret=None) -> dict:
    """POST to the token endpoint, surfacing invalid_grant.

    PKCE sends the client_id in the body; a confidential web app authenticates
    with client_id/client_secret over HTTP Basic instead.
    """
    basic = (client_id, client_secret) if client_secret else None
    r = requests.post(TOKEN_URL, data=data, auth=basic,
                      headers={"Accept": "application/json"})
    if not r.ok:
        try:
            body = r.json()
        except ValueError:
            body = {}
        error = body.get("error") if isinstance(body, dict) else None
        if error == "invalid_grant":
            raise XeroAuthError(
                "Xero refused the grant (invalid_grant): the authorization expired "
                "or was revoked. Run `crude-xero auth` to re-consent.",
                status=r.status_code,
            )
        detail = body.get("error_description") or body.get("error") or r.text
        raise XeroAuthError(f"Xero token request failed: HTTP {r.status_code} {detail}",
                            status=r.status_code)
    return r.json()


def exchange_code(client_id, code, redirect_uri, code_verifier=None,
                  client_secret=None) -> dict:
    """Exchange an authorization code for a token set.

    Proof of the consent is the PKCE verifier for a public client, the HTTP
    Basic secret for a confidential web app; exactly one is passed.
    """
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if client_secret is None:
        data["client_id"] = client_id
        data["code_verifier"] = code_verifier
    return _token_request(data, client_id=client_id, client_secret=client_secret)


def refresh_token_grant(client_id, refresh_token, client_secret=None) -> dict:
    """Exchange a refresh token for a rotated token set."""
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    if client_secret is None:
        data["client_id"] = client_id
    return _token_request(data, client_id=client_id, client_secret=client_secret)


def list_connections(access_token) -> list:
    """List the tenants the access token can reach (Bearer only, no tenant header)."""
    r = requests.get(CONNECTIONS_URL, headers={
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    })
    r.raise_for_status()
    return r.json() if r.content else []


# ----------------------------------------------------------------------
# Interactive consent flows
# ----------------------------------------------------------------------


def _consent_url(client_id, redirect_uri, scopes, client_secret):
    """(state, verifier, url) for one consent: PKCE for a public client, none with a secret."""
    state = generate_state()
    verifier = None if client_secret else generate_pkce_verifier()
    challenge = pkce_challenge(verifier) if verifier else None
    return state, verifier, build_authorize_url(client_id, redirect_uri, scopes, state, challenge)


def loopback_authorize(client_id, redirect_uri, scopes, *, client_secret=None,
                       open_browser=True, timeout=300) -> dict:
    """Run the loopback consent flow and return a token set.

    The shared loopback server (crude_common.oauth_consent) serves one callback
    on the redirect_uri's localhost port and validates the state; the captured
    code is exchanged with a PKCE verifier for a public client, with the secret
    for a confidential web app.
    """
    state, verifier, url = _consent_url(client_id, redirect_uri, scopes, client_secret)
    code = loopback_code(url, redirect_uri, state, error=XeroAuthError,
                         open_browser=open_browser, timeout=timeout, title="crude-xero")
    return exchange_code(client_id, code, redirect_uri, verifier,
                         client_secret=client_secret)


def manual_authorize(client_id, redirect_uri, scopes, *, client_secret=None) -> dict:
    """Paste-based consent for a headless box: print the URL, read the redirect back.

    Accepts either the full pasted redirect URL (state validated) or a bare code.
    """
    state, verifier, url = _consent_url(client_id, redirect_uri, scopes, client_secret)
    code = manual_code(url, state, error=XeroAuthError)
    return exchange_code(client_id, code, redirect_uri, verifier,
                         client_secret=client_secret)


# ----------------------------------------------------------------------
# Durable token store
# ----------------------------------------------------------------------


def token_store_path(account) -> Path:
    """The durable token side file, keyed by account, under $XDG_STATE_HOME.

    A rotating OAuth token is XDG *state*: it persists across restarts but is
    neither config (user-authored) nor cache (safe to delete — losing it costs a
    browser re-consent); see ``crude_common.statestore``. The default account
    keeps ``xero_token.json``; a named account uses ``xero_token_<account>.json``.
    """
    return state_path("xero_token.json" if not account else f"xero_token_{account}.json")


def _seed_expiry(timestamp):
    """Map a config `timestamp` to (obtained_at, expires_at) epoch seconds.

    A timezone-aware ISO timestamp yields a real 30-minute window; a naive or
    unparseable one is untrustworthy as UTC, so treat the seeded access token as
    already expired (forcing a refresh) while keeping the refresh token.
    """
    now = time.time()
    if isinstance(timestamp, datetime):
        dt = timestamp
    elif isinstance(timestamp, str):
        try:
            dt = datetime.fromisoformat(timestamp)
        except ValueError:
            dt = None
    else:
        dt = None
    if dt is not None and dt.tzinfo is not None:
        obtained = dt.timestamp()
        return obtained, obtained + _ACCESS_TTL
    return now, now - 1


def load_tokens(account, xero_section) -> dict | None:
    """Return the durable token set for an account.

    The side file is authoritative once it exists. Otherwise the config
    `access_token`/`refresh_token`/`timestamp` are read once as a migration seed,
    written to the side file, and returned. None when no refresh token is found
    anywhere.
    """
    path = token_store_path(account)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            pass
    refresh = xero_section.get("refresh_token")
    if not refresh:
        return None
    obtained_at, expires_at = _seed_expiry(xero_section.get("timestamp"))
    tokens = {
        "access_token": xero_section.get("access_token"),
        "refresh_token": refresh,
        "obtained_at": obtained_at,
        "expires_at": expires_at,
        "scope": _scope_str(xero_section.get("scopes", "")),
    }
    save_tokens(account, tokens)
    return tokens


def save_tokens(account, tokens) -> None:
    """Persist a token set atomically (mode 0600) to the durable side file.

    A file that cannot be written ends the command with one line naming it.
    After a refresh that loses the rotated refresh token, which was in memory
    only; whether to carry on with it instead is open.
    """
    write_or_exit(token_store_path(account), json.dumps(tokens, indent=2))


@contextlib.contextmanager
def token_lock(account):
    """Hold an exclusive flock on ``<token path>.lock`` for the duration."""
    path = token_store_path(account)
    lock_path = path.parent / (path.name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
