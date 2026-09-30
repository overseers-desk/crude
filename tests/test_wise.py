"""crude_wise tests: profile selection, the three paging shapes, and the SCA
sign-and-retry the balance statement needs.

No network. The session's request is replaced with canned responses in the
repo's standard shape. The one thing that leaves the process is openssl, signing
a throwaway key made for the test and verified with the same tool.
"""

from __future__ import annotations

import base64
import shutil
import subprocess

import pytest
import typer

from crude_wise import cli, cli_resources, client
from crude_wise.client import WiseError, WiseSession, select_profile, sign_ott


class _Req:
    method = "GET"
    path_url = "/x"


class _Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = {} if body is None else body
        self.headers = headers or {}
        self.content = b"x"
        self.text = "err"
        self.request = _Req()

    def json(self):
        return self._body


def _session(**kw):
    return WiseSession("tok", **kw)


def _drive(monkeypatch, sess, responses):
    """Feed canned responses in order; record each call's url, params and headers."""
    calls = []

    def fake(method, url, **kw):
        # Copies: the walkers reuse and mutate one params dict across pages.
        calls.append({"url": url,
                      "params": dict(kw["params"]) if kw.get("params") else kw.get("params"),
                      "headers": dict(kw["headers"]) if kw.get("headers") else kw.get("headers")})
        return responses.pop(0)

    monkeypatch.setattr(sess.session, "request", fake)
    return calls


BIZ = {"id": 11, "type": "BUSINESS", "businessName": "Acme"}
BIZ2 = {"id": 12, "type": "BUSINESS", "businessName": "Beta"}
PERSONAL = {"id": 21, "type": "PERSONAL", "fullName": "A Person"}

OTT = "08945e57-3279-4bdf-870d-b37896e17ddb"


def _sca_403():
    return _Resp(403, headers={"x-2fa-approval": OTT, "x-2fa-approval-result": "REJECTED"})


# ----------------------------------------------------------------------
# Profile selection
# ----------------------------------------------------------------------

def test_one_business_profile_is_selected():
    assert select_profile([PERSONAL, BIZ], "[wise]") is BIZ


def test_two_business_profiles_need_profile_id():
    with pytest.raises(WiseError) as exc:
        select_profile([BIZ, BIZ2, PERSONAL], "[wise.ds]")
    assert "11 (Acme)" in str(exc.value) and "12 (Beta)" in str(exc.value)
    assert "[wise.ds]" in str(exc.value)


def test_a_lone_personal_profile_is_selected():
    assert select_profile([PERSONAL], "[wise]") is PERSONAL


def test_config_profile_id_wins_without_a_request(monkeypatch):
    sess = _session(profile_id="42")
    monkeypatch.setattr(sess.session, "request",
                        lambda *a, **k: pytest.fail("profiles were fetched"))
    assert sess.profile_id == 42


def test_profiles_are_fetched_once(monkeypatch):
    sess = _session()
    calls = _drive(monkeypatch, sess, [_Resp(body=[PERSONAL, BIZ])])
    assert sess.profile_id == 11
    assert sess.profile()["businessName"] == "Acme"
    assert len(calls) == 1
    assert calls[0]["url"].endswith("/2026Q3/profiles")


# ----------------------------------------------------------------------
# Paging
# ----------------------------------------------------------------------

def test_recipients_follow_seek_position(monkeypatch):
    sess = _session(profile_id=11)
    calls = _drive(monkeypatch, sess, [
        _Resp(body={"content": [{"id": 1}, {"id": 2}], "seekPositionForNext": 2}),
        _Resp(body={"content": [{"id": 3}], "seekPositionForNext": None}),
    ])
    assert [r["id"] for r in sess.iter_recipients()] == [1, 2, 3]
    assert calls[0]["params"]["profileId"] == 11
    assert "seekPosition" not in calls[0]["params"]
    assert calls[1]["params"]["seekPosition"] == 2


def test_activities_follow_the_cursor_until_null(monkeypatch):
    sess = _session(profile_id=11)
    calls = _drive(monkeypatch, sess, [
        _Resp(body={"activities": [{"id": "a"}], "cursor": "c1"}),
        _Resp(body={"activities": [{"id": "b"}], "cursor": None}),
    ])
    assert [a["id"] for a in sess.iter_activities()] == ["a", "b"]
    assert calls[1]["params"]["nextCursor"] == "c1"
    assert calls[1]["url"].endswith("/profiles/11/activities")


def test_transfers_page_by_offset_and_stop_on_a_short_page(monkeypatch):
    sess = _session(profile_id=11)
    monkeypatch.setattr(client, "TRANSFER_PAGE", 2)
    calls = _drive(monkeypatch, sess, [
        _Resp(body=[{"id": 1}, {"id": 2}]),
        _Resp(body=[{"id": 3}]),
    ])
    assert [t["id"] for t in sess.iter_transfers()] == [1, 2, 3]
    assert calls[0]["params"]["offset"] == 0 and calls[1]["params"]["offset"] == 2
    assert calls[0]["params"]["profile"] == 11


def test_max_items_stops_paging_early(monkeypatch):
    sess = _session(profile_id=11)
    calls = _drive(monkeypatch, sess, [
        _Resp(body={"content": [{"id": 1}, {"id": 2}], "seekPositionForNext": 2}),
    ])
    assert len(list(sess.iter_recipients(max_items=1))) == 1
    assert len(calls) == 1


# ----------------------------------------------------------------------
# SCA: the statement's 403 is signed and retried once
# ----------------------------------------------------------------------

def test_statement_403_is_signed_and_retried(monkeypatch):
    sess = _session(profile_id=11, private_key="k.pem")
    monkeypatch.setattr(client, "sign_ott", lambda ott, path: f"SIG({ott})")
    calls = _drive(monkeypatch, sess, [
        _sca_403(),
        _Resp(body={"transactions": [{"referenceNumber": "T1"}]}),
    ])
    stmt = sess.statement(5, "AUD", start="2026-09-01T00:00:00Z", end="2026-09-30T00:00:00Z")
    assert stmt["transactions"][0]["referenceNumber"] == "T1"
    assert len(calls) == 2
    assert not calls[0]["headers"]
    assert calls[1]["headers"]["x-2fa-approval"] == OTT
    assert calls[1]["headers"]["X-Signature"] == f"SIG({OTT})"
    assert calls[1]["params"]["currency"] == "AUD"


def test_statement_403_without_a_key_names_the_setup(monkeypatch):
    sess = _session(profile_id=11, account="ds")
    calls = _drive(monkeypatch, sess, [_sca_403()])
    with pytest.raises(WiseError) as exc:
        sess.statement(5, "AUD", start="s", end="e")
    assert "private_key" in str(exc.value) and "[wise.ds]" in str(exc.value)
    assert len(calls) == 1


def test_rejected_signature_is_reported_once(monkeypatch):
    sess = _session(profile_id=11, private_key="k.pem")
    monkeypatch.setattr(client, "sign_ott", lambda ott, path: "SIG")
    calls = _drive(monkeypatch, sess, [_sca_403(), _sca_403()])
    with pytest.raises(WiseError) as exc:
        sess.statement(5, "AUD", start="s", end="e")
    assert "public key" in str(exc.value)
    assert len(calls) == 2


def test_401_names_the_config_section(monkeypatch):
    sess = _session()
    _drive(monkeypatch, sess, [_Resp(401)])
    with pytest.raises(WiseError) as exc:
        sess.get("/profiles")
    assert exc.value.status == 401 and "[wise]" in str(exc.value)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not on PATH")
def test_sign_ott_produces_a_signature_openssl_verifies(tmp_path):
    priv, pub, sig = tmp_path / "k.pem", tmp_path / "k.pub", tmp_path / "ott.sig"
    subprocess.run(["openssl", "genrsa", "-out", str(priv), "2048"], check=True, capture_output=True)
    subprocess.run(["openssl", "rsa", "-in", str(priv), "-pubout", "-out", str(pub)],
                   check=True, capture_output=True)
    sig.write_bytes(base64.b64decode(sign_ott(OTT, priv)))
    done = subprocess.run(
        ["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature", str(sig)],
        input=OTT.encode("ascii"), capture_output=True,
    )
    assert done.returncode == 0, done.stderr


def test_sign_ott_missing_key(tmp_path):
    with pytest.raises(WiseError) as exc:
        sign_ott(OTT, tmp_path / "absent.pem")
    assert "not found" in str(exc.value)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def test_missing_api_token_is_a_clean_error(capsys):
    with pytest.raises(typer.Exit):
        cli._make_client({"wise": {}})
    assert "[wise] must set api_token" in capsys.readouterr().err


def test_make_client_reads_the_optional_keys():
    sess = cli._make_client({"wise": {
        "api_token": "t", "profile_id": 7, "private_key": "k.pem", "environment": "sandbox",
    }})
    assert sess.profile_id == 7
    assert sess.private_key == "k.pem"
    assert sess.base_url == "https://api.wise-sandbox.com/2026Q3"


def test_pick_balance_prefers_the_standard_balance_of_the_currency():
    bals = [
        {"id": 1, "currency": "AUD", "type": "SAVINGS"},
        {"id": 2, "currency": "AUD", "type": "STANDARD"},
        {"id": 3, "currency": "EUR", "type": "STANDARD"},
    ]
    assert cli_resources._pick_balance(bals, "aud", None)["id"] == 2
    assert cli_resources._pick_balance(bals, None, 1)["id"] == 1
    with pytest.raises(WiseError) as exc:
        cli_resources._pick_balance(bals, "USD", None)
    assert "AUD, EUR" in str(exc.value)


def test_activity_columns_strip_wise_markup():
    row = {"title": "<strong>Acme</strong>", "primaryAmount": "<positive>540 AUD</positive>"}
    cols = dict(cli_resources._ACTIVITY_COLS)
    assert cols["Title"](row) == "Acme"
    assert cols["Primary"](row) == "540 AUD"
