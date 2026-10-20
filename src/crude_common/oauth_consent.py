"""The browser half of an OAuth2 authorization-code consent, shared by the sites
that mint a user token (crude-xero, crude-business-profile).

A consent ends with the authorization server redirecting the browser to the URI
the client named, carrying a one-time code and the CSRF ``state`` the client
sent. Two ways to receive that redirect: a loopback web server that answers one
request on a localhost port, or, on a box with no browser or no reachable
loopback, the person pasting the redirect URL (or the bare code) back. Both
validate the state and hand back the code. Building the consent URL and
exchanging the code stay with the site: the endpoints, the PKCE choice and the
client's proof (a verifier, a secret) differ per server.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


def generate_state() -> str:
    """A CSRF state token for the authorize round-trip."""
    return secrets.token_urlsafe(24)


def generate_pkce_verifier() -> str:
    """A one-shot PKCE code_verifier (86 URL-safe chars, within the 43-128 range)."""
    return secrets.token_urlsafe(64)


def pkce_challenge(verifier) -> str:
    """The S256 code_challenge for a verifier: base64url(sha256(verifier)), unpadded."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def loopback_code(url, redirect_uri, state, *, error=RuntimeError, open_browser=True,
                  timeout=300, title="crude") -> str:
    """Open ``url`` for consent and return the code the redirect to ``redirect_uri`` carries.

    ``redirect_uri`` must be ``http://localhost:PORT/...`` (or 127.0.0.1); the
    server listens on that host and port, answers one request, and the page it
    shows names ``title``. The returned ``state`` must equal the one sent. Any
    failure raises ``error`` (the site's auth error class) with the reason.
    """
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1"):
        raise error(
            f"Loopback auth needs a redirect_uri like http://localhost:PORT/path; "
            f"got {redirect_uri!r}. Use the manual flow for a headless box."
        )
    host = parsed.hostname
    port = parsed.port or 80
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
                f"<html><body><h2>{title}</h2>"
                "<p>Authorization received. You can close this tab.</p>"
                "</body></html>".encode("utf-8")
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
        raise error(f"No authorization callback received within {timeout}s.")
    if captured.get("error"):
        raise error(f"Authorization failed: {captured['error']}.")
    if captured.get("state") != state:
        raise error("State mismatch on callback (possible CSRF); aborting.")
    code = captured.get("code")
    if not code:
        raise error("Callback carried no authorization code.")
    return code


def manual_code(url, state, *, error=RuntimeError) -> str:
    """Print ``url`` and return the code from the redirect URL (or bare code) pasted back.

    A pasted redirect URL has its ``state`` validated; a bare code has none to check.
    """
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
        raise error("State mismatch on the pasted redirect (possible CSRF); aborting.")
    if not code:
        raise error("No authorization code found in the pasted value.")
    return code
