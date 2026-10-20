"""The shared consent capture (crude_common.oauth_consent): no browser, no network.

Pins what the two consent paths promise their callers: the loopback server
serves one request on the redirect URI's port and hands back the code only when
the state matches, the manual path reads a pasted redirect URL or a bare code,
and every failure is raised as the error class the site passed in.
"""

from __future__ import annotations

import socket
import threading
import urllib.request

import pytest

from crude_common import oauth_consent as oc


class SiteError(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hit_later(url, delay=0.2):
    """Fetch ``url`` from a thread after ``delay``, once the server is listening."""
    t = threading.Timer(delay, lambda: urllib.request.urlopen(url, timeout=5).read())
    t.start()
    return t


def test_loopback_returns_the_code_when_state_matches():
    port = _free_port()
    redirect = f"http://127.0.0.1:{port}/cb"
    _hit_later(f"{redirect}?code=the-code&state=S1")
    code = oc.loopback_code("https://auth.example/consent", redirect, "S1",
                            open_browser=False, timeout=5, title="crude-test")
    assert code == "the-code"


def test_loopback_rejects_a_state_mismatch_with_the_site_error():
    port = _free_port()
    redirect = f"http://127.0.0.1:{port}/cb"
    _hit_later(f"{redirect}?code=the-code&state=OTHER")
    with pytest.raises(SiteError, match="State mismatch"):
        oc.loopback_code("https://auth.example/consent", redirect, "S1",
                         error=SiteError, open_browser=False, timeout=5)


def test_loopback_reports_the_servers_refusal():
    port = _free_port()
    redirect = f"http://127.0.0.1:{port}/cb"
    _hit_later(f"{redirect}?error=access_denied&state=S1")
    with pytest.raises(SiteError, match="access_denied"):
        oc.loopback_code("https://auth.example/consent", redirect, "S1",
                         error=SiteError, open_browser=False, timeout=5)


def test_loopback_needs_a_localhost_redirect():
    with pytest.raises(SiteError, match="Loopback auth needs"):
        oc.loopback_code("https://auth.example/consent", "https://example.com/cb", "S1",
                         error=SiteError, open_browser=False)


def test_manual_reads_a_pasted_redirect_url(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "http://localhost:1/cb?code=abc&state=S1")
    assert oc.manual_code("https://auth.example/consent", "S1") == "abc"


def test_manual_reads_a_bare_code(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "  bare-code  ")
    assert oc.manual_code("https://auth.example/consent", "S1") == "bare-code"


def test_manual_rejects_a_state_mismatch(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "http://localhost:1/cb?code=abc&state=X")
    with pytest.raises(SiteError, match="State mismatch"):
        oc.manual_code("https://auth.example/consent", "S1", error=SiteError)


def test_pkce_challenge_is_unpadded_s256():
    verifier = oc.generate_pkce_verifier()
    challenge = oc.pkce_challenge(verifier)
    assert "=" not in challenge and len(challenge) == 43
    assert oc.generate_state() != oc.generate_state()
