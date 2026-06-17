"""crude-airwallex bearer-token lifecycle — no network.

Airwallex auth is simpler than Xero's: an api-key login returns a ~30-minute
bearer with no refresh token, so there is nothing to rotate and no clobber rule.
These pin the 60s expiry skew, the ISO->epoch expiry mapping, the 0600 round-trip,
per-account file naming, the cache-on-login path, and that a rejected login raises
and writes no file. The store is redirected into a tmp dir via $XDG_STATE_HOME and
requests is monkeypatched, so nothing reaches the network.
"""

from __future__ import annotations

import os
import stat
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from crude_airwallex import auth
from crude_airwallex.client import AirwallexAuthError, AirwallexSession


@pytest.fixture
def token_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


def _session(token, account="acct"):
    return AirwallexSession(account, "cid", "key", base=auth.PROD_BASE, token=token)


def test_expired_guard_band():
    now = time.time()
    assert _session({"token": "t", "expires_at": now + 1000})._expired() is False
    assert _session({"token": "t", "expires_at": now + 30})._expired() is True   # inside skew
    assert _session({"token": "t", "expires_at": now - 100})._expired() is True
    assert _session(None)._expired() is True  # no token == expired


def test_base_url_selects_demo_for_demo_environments():
    assert auth.base_url(None) == auth.PROD_BASE
    assert auth.base_url("production") == auth.PROD_BASE
    assert auth.base_url("demo") == auth.DEMO_BASE
    assert auth.base_url("Sandbox") == auth.DEMO_BASE


def test_durable_token_maps_iso_expiry_to_epoch():
    grant = {"token": "abc", "expires_at": "2026-06-17T14:00:00+00:00"}
    tok = auth._durable_token(grant)
    assert tok["token"] == "abc"
    assert tok["expires_at"] == datetime(2026, 6, 17, 14, 0, tzinfo=timezone.utc).timestamp()
    assert "refresh_token" not in tok  # the model carries no refresh token


def test_durable_token_unparseable_expiry_falls_back_to_ttl():
    tok = auth._durable_token({"token": "abc", "expires_at": "garbage"})
    assert time.time() < tok["expires_at"] <= time.time() + auth._ACCESS_TTL + 1


def test_save_load_round_trip_is_0600(token_dir):
    tok = {"token": "abc", "expires_at": 123.0}
    auth.save_token("acct", tok)
    path = auth.token_store_path("acct")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert auth.load_token("acct") == tok


def test_per_account_file_naming():
    assert auth.token_store_path(None).name == "airwallex_token.json"
    assert auth.token_store_path("es").name == "airwallex_token_es.json"


def test_login_failure_raises_and_writes_no_token(token_dir, monkeypatch):
    resp = SimpleNamespace(
        ok=False, status_code=401, content=b"{}",
        json=lambda: {"message": "Invalid API key", "code": "unauthorized"},
    )
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: resp)
    with pytest.raises(AirwallexAuthError) as exc:
        auth.login("cid", "bad", base=auth.PROD_BASE)
    assert "Invalid API key" in str(exc.value)
    assert not auth.token_store_path(None).exists()


def test_session_login_caches_token(token_dir, monkeypatch):
    monkeypatch.setattr(
        auth, "login",
        lambda cid, key, *, base: {"token": "T", "expires_at": "2099-01-01T00:00:00Z"},
    )
    xs = _session(None)
    xs._ensure_token()
    assert xs.token["token"] == "T"
    assert auth.load_token("acct")["token"] == "T"  # persisted to the side file
