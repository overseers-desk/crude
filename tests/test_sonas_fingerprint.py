"""Each install presents its own device fingerprint to Sonas.

Sonas keeps one trust record per fingerprint, so two machines sharing one take
it from each other: a login from the second is refused, and its pending
verification then refuses the first machine's logins too (docs/sonas.md §3).
The fingerprint is therefore minted per install and kept in the state directory.
"""

import re
import stat

import pytest

from crude_common import config
from crude_sonas import auth, client


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    config.set_account(None)
    yield
    config.set_account(None)


def test_minted_once_and_kept(tmp_path):
    first = client.device_fingerprint()
    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert client.device_fingerprint() == first
    kept = tmp_path / "crude" / "sonas_fingerprint"
    assert kept.read_text() == first
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600


def test_two_installs_differ(tmp_path, monkeypatch):
    here = client.device_fingerprint()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "another-machine"))
    assert client.device_fingerprint() != here


def test_named_account_has_its_own():
    default = client.device_fingerprint()
    config.set_account("second")
    assert client.fingerprint_path().name == "sonas_fingerprint_second"
    assert client.device_fingerprint() != default


def _login_capturing(monkeypatch, sent):
    monkeypatch.setattr(client, "ddp_connect", lambda: {"store": {"users": {"u": {
        "profile": {"routes": [{"tenantId": "T"}]}}}}})

    def call(conn, method, params, timeout=30):
        sent.append((method, params))
        return {"token": "TOK"} if method == "login" else None
    monkeypatch.setattr(client, "ddp_call", call)
    monkeypatch.setattr(auth, "ddp_call", call)


def test_login_presents_the_install_fingerprint(monkeypatch):
    sent = []
    _login_capturing(monkeypatch, sent)
    client.SonasClient("a@example.com", "d" * 64)._ensure()
    assert sent[0][1][0]["fpIds"] == [client.device_fingerprint()]


def test_configured_fingerprint_wins_and_mints_nothing(monkeypatch):
    sent = []
    _login_capturing(monkeypatch, sent)
    client.SonasClient("a@example.com", "d" * 64, "f" * 32)._ensure()
    assert sent[0][1][0]["fpIds"] == ["f" * 32]
    assert not client.fingerprint_path().exists()


def test_verification_refusal_passes_on_what_sonas_said(monkeypatch):
    said = "We have re-sent you an email with a link to verify this location & device."

    def refuse(conn, method, params, timeout=30):
        raise RuntimeError({"error": "verification-error", "reason": said})
    monkeypatch.setattr(auth, "ddp_call", refuse)
    with pytest.raises(RuntimeError) as exc:
        auth.sonas_login({}, "a@example.com", "d" * 64, "f" * 32)
    assert said in str(exc.value) and "newest link" in str(exc.value)
