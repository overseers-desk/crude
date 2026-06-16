"""Xero OAuth2 flow and durable token store.

Xero is a confidential web app: the client_id/client_secret authenticate the
token endpoint over HTTP Basic (not PKCE). The refresh token rotates on every
refresh (single-use) and dies after 60 days idle, and there is no password to
silently re-login, so the rotating token is persisted to a durable, account-keyed
JSON side file in the config dir, written atomically under an flock, and never
clobbered on a failed refresh. Expiry is stored as epoch seconds, never a naive
datetime.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import tempfile
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from crude_common.config import find_config
from crude_xero.client import XeroAuthError

AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"

# Access tokens last 30 minutes; used when seeding from a parseable timestamp.
_ACCESS_TTL = 1800


def _scope_str(scopes) -> str:
    """Coerce scopes (list or pre-joined string) to a space-separated string."""
    return scopes if isinstance(scopes, str) else " ".join(scopes)


def generate_state() -> str:
    """A CSRF state token for the authorize round-trip."""
    return secrets.token_urlsafe(24)


def build_authorize_url(client_id, redirect_uri, scopes, state) -> str:
    """The Xero consent URL: response_type=code with client_id, redirect, scope, state."""
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": _scope_str(scopes),
        "state": state,
    })
    return f"{AUTHORIZE_URL}?{query}"


# ----------------------------------------------------------------------
# Token endpoint
# ----------------------------------------------------------------------


def _token_request(client_id, client_secret, data) -> dict:
    """POST to the token endpoint with HTTP Basic auth, surfacing invalid_grant."""
    r = requests.post(TOKEN_URL, data=data, auth=(client_id, client_secret),
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


def exchange_code(client_id, client_secret, code, redirect_uri) -> dict:
    """Exchange an authorization code for a token set."""
    return _token_request(client_id, client_secret, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    })


def refresh_token_grant(client_id, client_secret, refresh_token) -> dict:
    """Exchange a refresh token for a rotated token set."""
    return _token_request(client_id, client_secret, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })


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


def loopback_authorize(client_id, client_secret, redirect_uri, scopes, *,
                       open_browser=True, timeout=300) -> dict:
    """Run the loopback consent flow and return a token set.

    Parses host/port from the redirect_uri (must be http://localhost:PORT/...),
    serves one callback request, validates the returned state against the one
    sent, and exchanges the captured code.
    """
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1"):
        raise XeroAuthError(
            f"Loopback auth needs a redirect_uri like http://localhost:PORT/path; "
            f"got {redirect_uri!r}. Use the manual flow for a headless box."
        )
    host = parsed.hostname
    port = parsed.port or 80
    state = generate_state()
    url = build_authorize_url(client_id, redirect_uri, scopes, state)

    captured: dict = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            captured["code"] = (q.get("code") or [None])[0]
            captured["state"] = (q.get("state") or [None])[0]
            captured["error"] = (q.get("error") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>crude-xero</h2>"
                b"<p>Authorization received. You can close this tab.</p>"
                b"</body></html>"
            )

        def log_message(self, *args):
            pass

    server = HTTPServer((host, port), _Handler)
    server.timeout = timeout
    try:
        if open_browser:
            webbrowser.open(url)
        else:
            print(f"Open this URL to authorize:\n{url}")
        server.handle_request()
    finally:
        server.server_close()

    if not captured:
        raise XeroAuthError(f"No authorization callback received within {timeout}s.")
    if captured.get("error"):
        raise XeroAuthError(f"Authorization failed: {captured['error']}.")
    if captured.get("state") != state:
        raise XeroAuthError("State mismatch on callback (possible CSRF); aborting.")
    code = captured.get("code")
    if not code:
        raise XeroAuthError("Callback carried no authorization code.")
    return exchange_code(client_id, client_secret, code, redirect_uri)


def manual_authorize(client_id, client_secret, redirect_uri, scopes) -> dict:
    """Paste-based consent for a headless box: print the URL, read the redirect back.

    Accepts either the full pasted redirect URL (state validated) or a bare code.
    """
    state = generate_state()
    url = build_authorize_url(client_id, redirect_uri, scopes, state)
    print("Open this URL in a browser, authorize, then paste the redirect URL "
          "(or the bare code) back here:")
    print(url)
    print(f"(expected state: {state})")
    raw = input("redirect URL or code: ").strip()
    returned_state = None
    if raw.startswith(("http://", "https://")) or "code=" in raw:
        q = parse_qs(urlparse(raw).query)
        code = (q.get("code") or [None])[0]
        returned_state = (q.get("state") or [None])[0]
    else:
        code = raw
    if returned_state is not None and returned_state != state:
        raise XeroAuthError("State mismatch on the pasted redirect (possible CSRF); aborting.")
    if not code:
        raise XeroAuthError("No authorization code found in the pasted value.")
    return exchange_code(client_id, client_secret, code, redirect_uri)


# ----------------------------------------------------------------------
# Durable token store
# ----------------------------------------------------------------------


def token_store_path(account) -> Path:
    """The durable token side file, keyed by account, in the config dir."""
    base = find_config().parent
    return base / ("xero_token.json" if not account else f"xero_token_{account}.json")


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
    """Persist a token set atomically (temp file + fsync + os.replace, mode 0600)."""
    path = token_store_path(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".xero_token_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(tokens, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
