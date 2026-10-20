"""crude_business_profile transport unit tests (no network).

Pins the bearer header and its refresh (a recorded access token is used while
fresh, an expired one is refreshed before the first call, a 401 refreshes once
and retries), nextPageToken paging, location resolution by name, store code and
title, the review filters (since stops paging on update time, unreplied,
limit), the star and reply readings, and that Google's error message, not the
token, is what an error carries.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from crude_business_profile import client as c
from crude_business_profile.client import BusinessProfileError, BusinessProfileSession


def _resp(status, body=None, text=""):
    ok = 200 <= status < 300
    content = b"x" if body is not None else b""
    return SimpleNamespace(
        status_code=status, ok=ok, content=content, text=text, headers={},
        json=lambda: body if body is not None else (_ for _ in ()).throw(ValueError()))


class FakeHttp:
    """A requests.Session stand-in: answers by (url, pageToken) and records calls."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def request(self, method, url, **kw):
        params = kw.get("params") or {}
        self.calls.append((method, url, params, kw.get("headers") or {}, kw.get("json")))
        key = (url, params.get("pageToken"))
        answer = self.answers[key]
        if callable(answer):
            return answer()
        return answer


def _fresh_info(**extra):
    info = {"refresh_token": "rt", "client_id": "cid", "client_secret": "cs",
            "token": "recorded-token",
            "expiry": datetime.fromtimestamp(time.time() + 3000, timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
    info.update(extra)
    return info


LOC_URL = c.LOCATIONS_URL.format(account="accounts/1")
REV_URL = c.REVIEWS_URL.format(location="accounts/1/locations/9")


def _session(answers, info=None):
    http = FakeHttp(answers)
    sess = BusinessProfileSession(info or _fresh_info(), session=http)
    return sess, http


# -- credential ------------------------------------------------------------------


def test_recorded_token_is_used_while_fresh_and_sent_as_bearer():
    sess, http = _session({(c.ACCOUNTS_URL, None): _resp(200, {"accounts": []})})
    assert sess.accounts() == []
    assert http.calls[0][3]["Authorization"] == "Bearer recorded-token"


def test_expired_recorded_token_is_refreshed_before_the_first_call(monkeypatch):
    monkeypatch.setattr(c, "refresh_access_token", lambda info: ("fresh", time.time() + 3600))
    info = _fresh_info(expiry="2020-01-01T00:00:00Z")
    sess, http = _session({(c.ACCOUNTS_URL, None): _resp(200, {"accounts": []})}, info)
    sess.accounts()
    assert http.calls[0][3]["Authorization"] == "Bearer fresh"


def test_a_401_refreshes_once_and_retries(monkeypatch):
    monkeypatch.setattr(c, "refresh_access_token", lambda info: ("fresh", time.time() + 3600))
    answers = iter([_resp(401, {"error": {"message": "expired"}}), _resp(200, {"accounts": [{"name": "accounts/1"}]})])
    sess, http = _session({(c.ACCOUNTS_URL, None): lambda: next(answers)})
    assert sess.accounts() == [{"name": "accounts/1"}]
    assert [call[3]["Authorization"] for call in http.calls] == ["Bearer recorded-token", "Bearer fresh"]


def test_refresh_invalid_grant_names_the_consent(monkeypatch):
    resp = SimpleNamespace(ok=False, status_code=400, text="bad",
                           json=lambda: {"error": "invalid_grant"})
    monkeypatch.setattr(c.requests, "post", lambda *a, **k: resp)
    with pytest.raises(BusinessProfileError) as exc:
        c.refresh_access_token({"refresh_token": "dead", "client_id": "cid", "client_secret": "cs"})
    assert "invalid_grant" in str(exc.value) and "auth" in str(exc.value)


# -- transport -------------------------------------------------------------------


def test_pages_follow_next_page_token():
    answers = {
        (c.ACCOUNTS_URL, None): _resp(200, {"accounts": [{"name": "accounts/1"}]}),
        (LOC_URL, None): _resp(200, {"locations": [{"name": "locations/9", "title": "Cafe"}],
                                     "nextPageToken": "p2"}),
        (LOC_URL, "p2"): _resp(200, {"locations": [{"name": "locations/10", "title": "Stables"}]}),
    }
    sess, http = _session(answers)
    locs = sess.all_locations()
    assert [loc["resource"] for loc in locs] == ["accounts/1/locations/9", "accounts/1/locations/10"]
    assert http.calls[1][2]["readMask"] == c.LOCATION_READ_MASK


def test_errors_carry_googles_message_and_no_token():
    body = {"error": {"code": 403, "message": "Google My Business API has not been used in project 123",
                      "status": "PERMISSION_DENIED"}}
    sess, _ = _session({(c.ACCOUNTS_URL, None): _resp(403, body)})
    with pytest.raises(BusinessProfileError) as exc:
        sess.accounts()
    assert "has not been used" in str(exc.value) and exc.value.status == 403
    assert "recorded-token" not in str(exc.value)


# -- location resolution ----------------------------------------------------------


def _two_locations():
    return {
        (c.ACCOUNTS_URL, None): _resp(200, {"accounts": [{"name": "accounts/1"}]}),
        (LOC_URL, None): _resp(200, {"locations": [
            {"name": "locations/9", "title": "Riverside Cafe", "storeCode": "CAFE"},
            {"name": "locations/10", "title": "Riverside Stables"},
        ]}),
    }


def test_resolve_full_name_is_taken_as_given():
    sess, http = _session({})
    assert sess.resolve_location("accounts/5/locations/6") == "accounts/5/locations/6"
    assert http.calls == []


@pytest.mark.parametrize("spec", ["locations/9", "CAFE", "cafe", "riverside cafe"])
def test_resolve_by_name_store_code_or_title(spec):
    sess, _ = _session(_two_locations())
    assert sess.resolve_location(spec) == "accounts/1/locations/9"


def test_resolve_ambiguous_and_absent_name_the_candidates():
    sess, _ = _session(_two_locations())
    with pytest.raises(BusinessProfileError, match="several locations.*Stables"):
        sess.resolve_location("riverside")
    with pytest.raises(BusinessProfileError, match="no location matches.*Cafe"):
        sess.resolve_location("bakery")
    with pytest.raises(BusinessProfileError, match="pass --location"):
        sess.resolve_location(None)


def test_resolve_uses_the_default_then_the_only_location():
    sess, _ = _session(_two_locations())
    assert sess.resolve_location(None, "stables") == "accounts/1/locations/10"
    one = _two_locations()
    one[(LOC_URL, None)] = _resp(200, {"locations": [{"name": "locations/9", "title": "Cafe"}]})
    sess, _ = _session(one)
    assert sess.resolve_location(None) == "accounts/1/locations/9"


# -- reviews ------------------------------------------------------------------------


def _review(rid, created, updated=None, rating="FIVE", reply=None):
    r = {"reviewId": rid, "createTime": created, "updateTime": updated or created,
         "starRating": rating, "comment": f"text {rid}",
         "reviewer": {"displayName": f"user {rid}"}}
    if reply is not None:
        r["reviewReply"] = reply
    return r


def test_reviews_since_stops_paging_on_update_time_and_filters_on_write_time():
    page1 = [_review("a", "2026-10-01T00:00:00Z", "2026-10-05T00:00:00Z"),
             _review("b", "2026-09-01T00:00:00Z", "2026-10-03T00:00:00Z")]
    page2 = [_review("c", "2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"),
             _review("d", "2026-10-02T00:00:00Z", "2026-10-02T00:00:00Z")]
    answers = {(REV_URL, None): _resp(200, {"reviews": page1, "nextPageToken": "p2"}),
               (REV_URL, "p2"): _resp(200, {"reviews": page2})}
    sess, http = _session(answers)
    since = datetime(2026, 9, 15, tzinfo=timezone.utc)
    got = sess.reviews("accounts/1/locations/9", since=since)
    # b was written before `since` (though updated after); c stops the walk, so d is never seen.
    assert [r["reviewId"] for r in got] == ["a"]
    assert len(http.calls) == 2
    assert http.calls[0][2]["orderBy"] == "updateTime desc"


def test_reviews_unreplied_and_limit():
    reviews = [_review("a", "2026-10-01T00:00:00Z", reply={"comment": "thanks"}),
               _review("b", "2026-10-01T00:00:00Z", reply={"comment": "x", "reviewReplyState": "REJECTED"}),
               _review("c", "2026-10-01T00:00:00Z"),
               _review("d", "2026-10-01T00:00:00Z")]
    sess, _ = _session({(REV_URL, None): _resp(200, {"reviews": reviews})})
    assert [r["reviewId"] for r in sess.reviews("accounts/1/locations/9", unreplied=True)] == ["b", "c", "d"]
    assert [r["reviewId"] for r in sess.reviews("accounts/1/locations/9", limit=2)] == ["a", "b"]


def test_stars_and_replied_readings():
    assert c.stars({"starRating": "FOUR"}) == 4
    assert c.stars({"starRating": "SIX"}) is None
    assert c.replied({"reviewReply": {"comment": "x"}}) is True
    assert c.replied({"reviewReply": {"comment": "x", "reviewReplyState": "REJECTED"}}) is False
    assert c.replied({}) is False


def test_reply_and_unreply_hit_the_reply_subresource():
    url = f"{REV_URL}/r1/reply"
    answers = {(url, None): _resp(200, {"comment": "hello"})}
    sess, http = _session(answers)
    assert sess.reply("accounts/1/locations/9", "r1", "hello") == {"comment": "hello"}
    sess.delete_reply("accounts/1/locations/9", "r1")
    assert [(m, j) for m, _, _, _, j in http.calls] == [("PUT", {"comment": "hello"}), ("DELETE", None)]


def test_address_line_and_place_id():
    loc = {"storefrontAddress": {"addressLines": ["1 River Rd"], "locality": "Mudgee",
                                 "administrativeArea": "NSW", "postalCode": "2850"},
           "metadata": {"placeId": "ChIJx"}}
    assert c.address_line(loc) == "1 River Rd, Mudgee, NSW, 2850"
    assert c.place_id(loc) == "ChIJx"
    assert c.address_line({}) == "" and c.place_id({}) is None
