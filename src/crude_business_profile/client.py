"""Business Profile transport: a bearer session over Google's Business Profile API hosts.

One BusinessProfileSession carries the authorised-user token record (the side
file's JSON) and mints the hour-long access token from its refresh token when
the one in the record has expired or a call answers 401. The refresh token does
not rotate on use, so nothing is written back; the record is read-only here.

The APIs sit on separate hosts under one token: accounts on Account Management
v1, locations on Business Information v1, and reviews on the v4 endpoint, which
the v1 APIs have no counterpart for. Each list pages through ``nextPageToken`` at its
API's largest page size. A review's location is named for v4 as
``accounts/<id>/locations/<id>``, the account's name joined to the location's.
"""

from __future__ import annotations

import time

import requests

from crude_common.httpapi import HttpSession
from crude_common.localtime import parse_iso_utc

SCOPE = "https://www.googleapis.com/auth/business.manage"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
LOCATIONS_URL = "https://mybusinessbusinessinformation.googleapis.com/v1/{account}/locations"
REVIEWS_URL = "https://mybusiness.googleapis.com/v4/{location}/reviews"
LOCATION_READ_MASK = "name,title,storeCode,storefrontAddress,metadata"

ACCOUNT_PAGE = 20
LOCATION_PAGE = 100
REVIEW_PAGE = 50

STAR_RATINGS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}

# Refresh this close to the access token's expiry rather than risk a 401.
_SKEW = 60

AUTH_HINT = "run `crude-business-profile auth` to consent again"


class BusinessProfileError(RuntimeError):
    """A Google API error, carrying the HTTP status; the message is Google's own,
    which names the cause (an API not enabled, no quota, no access to a location)."""

    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


def refresh_access_token(info: dict) -> tuple:
    """(access_token, expires_at epoch seconds) from the record's refresh token.

    ``invalid_grant`` means the refresh token was revoked or expired (a consent
    screen left in Testing mode expires them after seven days); the message says
    to consent again.
    """
    data = {
        "grant_type": "refresh_token",
        "client_id": info.get("client_id"),
        "client_secret": info.get("client_secret"),
        "refresh_token": info.get("refresh_token"),
    }
    r = requests.post(info.get("token_uri") or TOKEN_URL, data=data,
                      headers={"Accept": "application/json"}, timeout=30)
    if not r.ok:
        try:
            body = r.json()
        except ValueError:
            body = {}
        error = body.get("error") if isinstance(body, dict) else None
        if error == "invalid_grant":
            raise BusinessProfileError(
                f"Google refused the grant (invalid_grant): the refresh token was revoked "
                f"or has expired; {AUTH_HINT}.", status=r.status_code)
        detail = body.get("error_description") or error or r.text
        raise BusinessProfileError(
            f"Google token request failed: HTTP {r.status_code} {detail}", status=r.status_code)
    grant = r.json()
    return grant["access_token"], time.time() + int(grant.get("expires_in", 3600))


def recorded_access_token(info: dict):
    """(access_token, expires_at) from the record itself while its ``expiry`` is
    comfortably ahead, else None. google-auth writes ``expiry`` as a naive UTC
    ISO instant with a trailing Z."""
    token = info.get("token")
    expiry = parse_iso_utc(info.get("expiry"))
    if not token or expiry is None:
        return None
    expires_at = expiry.timestamp()
    if expires_at - time.time() < _SKEW:
        return None
    return token, expires_at


class BusinessProfileSession(HttpSession):
    def __init__(self, info: dict, *, session=None, timeout=30):
        super().__init__("", session=session, timeout=timeout)
        self.info = info
        self.access_token = None
        self.expires_at = 0.0
        recorded = recorded_access_token(info)
        if recorded:
            self.access_token, self.expires_at = recorded

    # -- credential ---------------------------------------------------------

    def _refresh(self) -> None:
        self.access_token, self.expires_at = refresh_access_token(self.info)

    def _ensure_token(self) -> None:
        if not self.access_token or self.expires_at - time.time() < _SKEW:
            self._refresh()

    def _on_401(self, r) -> bool:
        self._refresh()
        return True

    # -- transport ----------------------------------------------------------

    def _build_url(self, path) -> str:
        return path  # every path here is an absolute URL on one of the API hosts

    def _auth_headers(self, extra):
        headers = {"Authorization": f"Bearer {self.access_token}"}
        if extra:
            headers.update(extra)
        return headers

    def _raise(self, r) -> None:
        try:
            body = r.json()
        except ValueError:
            body = {}
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            detail = err.get("message") or err.get("status") or r.text[:300]
        else:
            detail = err or r.text[:300]
        hint = f"; {AUTH_HINT}" if r.status_code == 401 else ""
        raise BusinessProfileError(
            f"Business Profile API returned {r.status_code}: {detail}{hint}",
            status=r.status_code)

    def _pages(self, url, params, key):
        """Yield the items under ``key`` from every page of a list call."""
        p = dict(params)
        while True:
            page = self._get(url, params=p)
            for item in page.get(key) or []:
                yield item
            token = page.get("nextPageToken")
            if not token:
                return
            p["pageToken"] = token

    # -- accounts and locations ---------------------------------------------

    def accounts(self) -> list:
        """The accounts the signed-in Google account manages (``accounts/<id>``)."""
        return list(self._pages(ACCOUNTS_URL, {"pageSize": ACCOUNT_PAGE}, "accounts"))

    def locations(self, account_name: str) -> list:
        """One account's locations, each with ``account`` and its v4 ``resource`` name added."""
        url = LOCATIONS_URL.format(account=account_name)
        params = {"pageSize": LOCATION_PAGE, "readMask": LOCATION_READ_MASK}
        out = []
        for loc in self._pages(url, params, "locations"):
            row = dict(loc)
            row["account"] = account_name
            row["resource"] = f"{account_name}/{loc.get('name', '')}"
            out.append(row)
        return out

    def all_locations(self) -> list:
        return [loc for acct in self.accounts() for loc in self.locations(acct["name"])]

    def resolve_location(self, spec, default=None) -> str:
        """The v4 resource name of the location ``spec`` names.

        ``spec`` (or ``default``, the config's ``default_location``, when spec is
        None) is a full ``accounts/<id>/locations/<id>`` name, taken as given; a
        ``locations/<id>`` name; a store code; or a case-insensitive substring of
        the title. With neither given, the one location the account manages is
        it, and several is an error naming them so the caller can pick.
        """
        spec = spec or default
        if spec and spec.startswith("accounts/") and "/locations/" in spec:
            return spec
        locations = self.all_locations()
        if not locations:
            raise BusinessProfileError("the token's accounts manage no locations")
        if not spec:
            if len(locations) == 1:
                return locations[0]["resource"]
            raise BusinessProfileError(
                "several locations; pass --location, or set default_location: "
                + ", ".join(_label(loc) for loc in locations))
        needle = spec.lower()
        matches = [
            loc for loc in locations
            if loc.get("name") == spec or loc.get("storeCode") == spec
            or needle in (loc.get("title") or "").lower()
        ]
        if len(matches) == 1:
            return matches[0]["resource"]
        if not matches:
            raise BusinessProfileError(
                f"no location matches {spec!r}; the account manages: "
                + ", ".join(_label(loc) for loc in locations))
        raise BusinessProfileError(
            f"{spec!r} matches several locations: " + ", ".join(_label(m) for m in matches))

    # -- reviews --------------------------------------------------------------

    def iter_reviews(self, location: str):
        """Every review of a location, newest update first, across pages."""
        url = REVIEWS_URL.format(location=location)
        params = {"pageSize": REVIEW_PAGE, "orderBy": "updateTime desc"}
        return self._pages(url, params, "reviews")

    def reviews(self, location: str, *, since=None, unreplied=False, limit=None) -> list:
        """Reviews of a location, newest update first, with the filters applied.

        ``since`` is an aware datetime: only reviews written at or after it are
        kept, and since the list is ordered by update time, which is never
        earlier than the write time, paging stops at the first review updated
        before it. ``unreplied`` keeps those awaiting the business's reply.
        ``limit`` caps the count kept; None keeps every one.
        """
        out = []
        for review in self.iter_reviews(location):
            if since is not None:
                updated = parse_iso_utc(review.get("updateTime"))
                if updated is not None and updated < since:
                    break
                created = parse_iso_utc(review.get("createTime"))
                if created is not None and created < since:
                    continue
            if unreplied and replied(review):
                continue
            out.append(review)
            if limit is not None and len(out) >= limit:
                break
        return out

    def review(self, location: str, review_id: str) -> dict:
        return self._get(f"{REVIEWS_URL.format(location=location)}/{review_id}")

    def reply(self, location: str, review_id: str, comment: str) -> dict:
        """Write (or replace) the business's public reply to a review."""
        return self._put(f"{REVIEWS_URL.format(location=location)}/{review_id}/reply",
                         json={"comment": comment})

    def delete_reply(self, location: str, review_id: str) -> dict:
        return self._delete(f"{REVIEWS_URL.format(location=location)}/{review_id}/reply")


# -- record shapes --------------------------------------------------------------


def _label(loc: dict) -> str:
    code = f" [{loc['storeCode']}]" if loc.get("storeCode") else ""
    return f"{loc.get('title') or '?'}{code} ({loc.get('resource')})"


def stars(review: dict):
    """The star rating as 1..5, or None for a rating outside Google's five words."""
    return STAR_RATINGS.get(review.get("starRating"))


def replied(review: dict) -> bool:
    """True when the business's reply stands: one Google rejected is not public,
    so the review still awaits a reply."""
    reply = review.get("reviewReply")
    return reply is not None and reply.get("reviewReplyState") != "REJECTED"


def address_line(loc: dict) -> str:
    addr = loc.get("storefrontAddress") or {}
    parts = list(addr.get("addressLines") or [])
    parts += [addr.get("locality"), addr.get("administrativeArea"), addr.get("postalCode")]
    return ", ".join(p for p in parts if p)


def place_id(loc: dict):
    return (loc.get("metadata") or {}).get("placeId")
