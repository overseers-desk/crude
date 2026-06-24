"""crude_facebook transport unit tests (no network).

Pins the auth-param injection, the appsecret_proof, the Graph error-code mapping,
cursor pagination, the config page-id fallback (so calls work when /me/accounts is
empty), and the insight-value shapes.
"""

from types import SimpleNamespace

import pytest

from crude_facebook.client import (
    FacebookError,
    FacebookSession,
    appsecret_proof,
    insight_value,
)


def _resp(status, error):
    """A requests-Response stand-in carrying one Graph error, for the _raise tests."""
    return SimpleNamespace(
        status_code=status, text="", json=lambda: {"error": error})


def test_params_carries_token_and_proof():
    s = FacebookSession("tok", app_secret="secret")
    p = s._params("tok", {"fields": "id"})
    assert p["access_token"] == "tok"
    assert p["appsecret_proof"] == appsecret_proof("tok", "secret")
    assert p["fields"] == "id"


def test_params_omits_proof_without_secret():
    s = FacebookSession("tok")
    assert "appsecret_proof" not in s._params("tok", None)


def test_appsecret_proof_is_hmac_sha256():
    import hashlib
    import hmac

    expected = hmac.new(b"secret", b"tok", hashlib.sha256).hexdigest()
    assert appsecret_proof("tok", "secret") == expected


@pytest.mark.parametrize(
    "code,needle",
    [(190, "190"), (210, "Page access token"), (10, "Permission"),
     (4, "Rate limited"), (100, "code 100")],
)
def test_raise_maps_graph_error_codes(code, needle):
    s = FacebookSession("tok")
    with pytest.raises(FacebookError) as e:
        s._raise(_resp(400, {"message": "boom", "code": code}))
    assert needle in str(e.value)
    assert e.value.code == code


def test_page_id_from_config_needs_no_network():
    s = FacebookSession("tok", page_id="P1")
    assert s.page_id == "P1"


def test_iter_edge_follows_the_after_cursor():
    s = FacebookSession("tok", page_id="P")
    pages = [
        {"data": [{"id": 1}, {"id": 2}],
         "paging": {"next": "u", "cursors": {"after": "c1"}}},
        {"data": [{"id": 3}], "paging": {}},
    ]
    seen_after = []

    def fake_call(method, path, token, *, params=None):
        seen_after.append((params or {}).get("after"))
        return pages[len(seen_after) - 1]

    s._call = fake_call
    out = [i["id"] for i in s.iter_edge("/x", token="tok")]
    assert out == [1, 2, 3]
    assert seen_after == [None, "c1"]


def test_iter_edge_honours_max_items():
    s = FacebookSession("tok")
    s._call = lambda *a, **k: {
        "data": [{"id": 1}, {"id": 2}, {"id": 3}],
        "paging": {"next": "u", "cursors": {"after": "c"}},
    }
    assert len(list(s.iter_edge("/x", token="tok", max_items=2))) == 2


def test_insight_value_handles_both_shapes():
    assert insight_value({"total_value": {"value": 5}}) == 5
    assert insight_value({"values": [{"value": 1}, {"value": 2}]}) == 2
    assert insight_value({}) is None
