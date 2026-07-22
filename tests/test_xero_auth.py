"""Unit tests for the crude-xero OAuth token lifecycle — no network.

These pin the durable-token-store invariants: the 60s expiry skew, the
refresh that persists rotated tokens on success and never clobbers the stored
token on failure, the concurrent-rotation "adopt" path that skips the single-use
grant, the config->side-file migration seed (including the naive-timestamp =
already-expired rule), the 0600 round-trip, and the authorize-URL shape. The
token store is redirected into a tmp dir by pointing `$XDG_STATE_HOME` there,
and the token endpoint / grant are monkeypatched, so nothing reaches the network.
"""

from __future__ import annotations

import os
import stat
import time
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from crude_xero import auth
from crude_xero.client import XeroAuthError, XeroSession


@pytest.fixture
def token_dir(tmp_path, monkeypatch):
    """Point token_store_path under tmp_path via $XDG_STATE_HOME.

    token_store_path resolves to ``$XDG_STATE_HOME/crude/xero_token[_<account>].json``,
    so the store lands in ``tmp_path/crude/``; save_tokens creates that dir.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


def _session(tokens, account="acct"):
    return XeroSession(account, "client-id", tokens)


# ----------------------------------------------------------------------
# _expired — 60s skew
# ----------------------------------------------------------------------


def test_expired_around_expiry():
    now = time.time()
    assert _session({"expires_at": now + 1000})._expired() is False  # comfortably valid
    assert _session({"expires_at": now + 30})._expired() is True     # inside the 60s skew
    assert _session({"expires_at": now - 100})._expired() is True    # already past
    assert _session({})._expired() is True                           # no expiry == expired


# ----------------------------------------------------------------------
# _refresh — persist on success, never clobber on failure, adopt on race
# ----------------------------------------------------------------------


def test_refresh_success_persists_rotated_tokens(token_dir, monkeypatch):
    xs = _session({"access_token": "old-acc", "refresh_token": "old-ref",
                   "expires_at": time.time() - 10, "scope": "openid"})
    grant_args = {}

    def fake_grant(client_id, refresh_token, client_secret=None):
        grant_args.update(client_id=client_id, refresh_token=refresh_token,
                          client_secret=client_secret)
        return {"access_token": "new-acc", "refresh_token": "new-ref",
                "expires_in": 1800, "scope": "openid accounting"}

    monkeypatch.setattr(auth, "refresh_token_grant", fake_grant)

    before = time.time()
    xs._refresh()

    # The single-use refresh token was spent.
    assert grant_args["refresh_token"] == "old-ref"
    assert grant_args["client_id"] == "client-id"
    # In-memory tokens rotated, with a fresh ~30-minute window.
    assert xs.tokens["access_token"] == "new-acc"
    assert xs.tokens["refresh_token"] == "new-ref"
    assert before + 1800 <= xs.tokens["expires_at"] <= time.time() + 1800
    # Persisted to the side file (read back independently).
    assert auth.load_tokens("acct", {}) == xs.tokens


def test_refresh_failure_does_not_clobber_stored_token(token_dir, monkeypatch):
    original = {"access_token": "old-acc", "refresh_token": "old-ref",
                "obtained_at": time.time() - 100, "expires_at": time.time() - 10,
                "scope": "openid"}
    auth.save_tokens("acct", original)
    xs = _session(dict(original))

    def fake_grant(*a, **k):
        raise XeroAuthError("Xero refused the grant (invalid_grant).")

    monkeypatch.setattr(auth, "refresh_token_grant", fake_grant)

    with pytest.raises(XeroAuthError):
        xs._refresh()

    # The stored token is untouched after the failed refresh.
    assert auth.load_tokens("acct", {}) == original


def test_refresh_adopts_newer_side_file_token_without_granting(token_dir, monkeypatch):
    # A concurrent process already rotated to a still-valid token in the side file.
    newer = {"access_token": "adopted-acc", "refresh_token": "adopted-ref",
             "obtained_at": time.time(), "expires_at": time.time() + 9999,
             "scope": "openid"}
    auth.save_tokens("acct", newer)
    xs = _session({"access_token": "stale", "refresh_token": "stale-ref",
                   "expires_at": time.time() - 10})

    def fake_grant(*a, **k):
        raise AssertionError("grant must not be called when adopting a fresh token")

    monkeypatch.setattr(auth, "refresh_token_grant", fake_grant)

    xs._refresh()

    assert xs.tokens == newer  # adopted the side-file token, no grant


def test_refresh_token_grant_invalid_grant_raises(monkeypatch):
    resp = SimpleNamespace(ok=False, status_code=400, text="bad",
                           json=lambda: {"error": "invalid_grant"})
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: resp)

    with pytest.raises(XeroAuthError) as exc:
        auth.refresh_token_grant("client-id", "dead-ref")
    assert "invalid_grant" in str(exc.value)


# ----------------------------------------------------------------------
# load_tokens / save_tokens — the config-seed migration and the side file
# ----------------------------------------------------------------------


def test_load_tokens_seeds_from_section_and_writes_file(token_dir):
    ts = "2026-06-17T10:00:00+00:00"
    section = {"access_token": "seed-acc", "refresh_token": "seed-ref",
               "timestamp": ts, "scopes": ["openid", "accounting"]}

    tokens = auth.load_tokens("acct", section)

    assert tokens["access_token"] == "seed-acc"
    assert tokens["refresh_token"] == "seed-ref"
    assert tokens["scope"] == "openid accounting"  # list coerced to space-joined
    # A tz-aware timestamp yields a real 30-minute window.
    obtained = datetime.fromisoformat(ts).timestamp()
    assert tokens["obtained_at"] == obtained
    assert tokens["expires_at"] == obtained + 1800
    # Seed was persisted to the side file.
    assert auth.token_store_path("acct").exists()
    assert auth.load_tokens("acct", {}) == tokens


def test_load_tokens_none_without_refresh_token(token_dir):
    assert auth.load_tokens("acct", {}) is None
    assert auth.load_tokens("acct", {"access_token": "a"}) is None  # no refresh_token


def test_load_tokens_naive_timestamp_seeds_already_expired(token_dir):
    # A naive timestamp is untrustworthy as UTC: seed the access token as expired
    # (forcing a refresh) while keeping the refresh token.
    section = {"access_token": "seed-acc", "refresh_token": "seed-ref",
               "timestamp": "2026-06-17T10:00:00"}

    tokens = auth.load_tokens("acct", section)

    assert tokens["refresh_token"] == "seed-ref"
    assert tokens["expires_at"] < time.time()


def test_save_tokens_is_0600_and_round_trips(token_dir):
    tokens = {"access_token": "a", "refresh_token": "r",
              "obtained_at": 1.0, "expires_at": 2.0, "scope": "openid"}
    auth.save_tokens("acct", tokens)

    path = auth.token_store_path("acct")
    assert path.exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert auth.load_tokens("acct", {}) == tokens


# ----------------------------------------------------------------------
# build_authorize_url / generate_state
# ----------------------------------------------------------------------


def test_build_authorize_url_carries_oauth_params():
    url = auth.build_authorize_url(
        "CID", "http://localhost:8976/callback",
        ["openid", "accounting.transactions"], "STATE-XYZ", "CHALLENGE")
    assert url.startswith(auth.AUTHORIZE_URL + "?")
    q = parse_qs(urlparse(url).query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["CID"]
    assert q["redirect_uri"] == ["http://localhost:8976/callback"]
    assert q["scope"] == ["openid accounting.transactions"]  # list joined with spaces
    assert q["state"] == ["STATE-XYZ"]
    assert q["code_challenge"] == ["CHALLENGE"]
    assert q["code_challenge_method"] == ["S256"]


def test_build_authorize_url_accepts_prejoined_scope_string():
    url = auth.build_authorize_url("CID", "http://localhost/cb", "openid email", "S", "CH")
    q = parse_qs(urlparse(url).query)
    assert q["scope"] == ["openid email"]


def test_generate_state_is_nonempty_and_unique():
    s1, s2 = auth.generate_state(), auth.generate_state()
    assert s1 and s2
    assert s1 != s2


def test_pkce_challenge_is_unpadded_s256_of_verifier():
    import base64
    import hashlib
    verifier = auth.generate_pkce_verifier()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    assert auth.pkce_challenge(verifier) == expected
    assert "=" not in auth.pkce_challenge(verifier)


# ----------------------------------------------------------------------
# Confidential web-app flow — config client_secret selects it
# ----------------------------------------------------------------------


def test_authorize_url_without_challenge_omits_pkce_params():
    url = auth.build_authorize_url("CID", "http://localhost/cb", "openid", "S")
    q = parse_qs(urlparse(url).query)
    assert "code_challenge" not in q
    assert "code_challenge_method" not in q


def test_exchange_code_with_secret_uses_basic_auth_and_no_verifier(monkeypatch):
    seen = {}

    def fake_post(url, data=None, auth=None, headers=None):
        seen.update(data=data, auth=auth)
        return SimpleNamespace(ok=True, content=b"{}", json=lambda: {"access_token": "a"})

    monkeypatch.setattr(auth.requests, "post", fake_post)
    auth.exchange_code("CID", "CODE", "http://localhost/cb", client_secret="SEC")
    assert seen["auth"] == ("CID", "SEC")
    assert "code_verifier" not in seen["data"]
    assert "client_id" not in seen["data"]  # carried by Basic auth instead


def test_refresh_grant_with_secret_uses_basic_auth(monkeypatch):
    seen = {}

    def fake_post(url, data=None, auth=None, headers=None):
        seen.update(data=data, auth=auth)
        return SimpleNamespace(ok=True, content=b"{}", json=lambda: {"access_token": "a"})

    monkeypatch.setattr(auth.requests, "post", fake_post)
    auth.refresh_token_grant("CID", "REF", client_secret="SEC")
    assert seen["auth"] == ("CID", "SEC")
    assert seen["data"] == {"grant_type": "refresh_token", "refresh_token": "REF"}


def test_session_refresh_passes_configured_secret(token_dir, monkeypatch):
    xs = XeroSession("acct", "client-id",
                     {"access_token": "old", "refresh_token": "old-ref",
                      "expires_at": time.time() - 10},
                     client_secret="SEC")
    grant_args = {}

    def fake_grant(client_id, refresh_token, client_secret=None):
        grant_args.update(client_secret=client_secret)
        return {"access_token": "new", "refresh_token": "new-ref", "expires_in": 1800}

    monkeypatch.setattr(auth, "refresh_token_grant", fake_grant)
    xs._refresh()
    assert grant_args["client_secret"] == "SEC"
