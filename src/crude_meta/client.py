"""Meta Graph API transport: one token, lazy Page/Instagram discovery.

A MetaSession carries the configured bearer token and resolves the Page (and its
linked Instagram Business account) lazily from ``/me/accounts`` on first use, the
direct analogue of how CloverSession resolves its merchant from
``/v3/merchants/current``. Page and Instagram writes need a Page access token, so
the session prefers the per-Page token that ``/me/accounts`` returns and falls
back to the configured token (with explicit ``page_id``/``ig_user_id`` config
keys) when that edge is empty — the case the 2026-06-24 spike hit.

The Graph API carries auth as the ``access_token`` query parameter and, when the
app enforces it, an ``appsecret_proof`` signature; both ride on every call rather
than on a session header, because the token varies (user token for discovery,
Page token for everything else).
"""

from __future__ import annotations

import hashlib
import hmac

from crude_common.httpapi import HttpSession

GRAPH = "https://graph.facebook.com"
API_VERSION = "v25.0"

# Field sets requested by the read commands. media get/list always carry both
# `id` and `shortcode` so a consumer can bridge a Graph media id to its 19-digit
# media_pk (base64url-decode of the shortcode).
MEDIA_FIELDS = (
    "id,shortcode,caption,media_type,media_product_type,permalink,timestamp,"
    "like_count,comments_count,thumbnail_url,media_url"
)
IG_ACCOUNT_FIELDS = (
    "id,username,name,biography,website,followers_count,follows_count,"
    "media_count,profile_picture_url"
)
IG_COMMENT_FIELDS = "id,text,username,timestamp,like_count,hidden"

FB_POST_FIELDS = "id,message,created_time,permalink_url,is_published,full_picture"
FB_PAGE_FIELDS = (
    "id,name,about,category,link,website,phone,emails,followers_count,fan_count"
)
FB_COMMENT_FIELDS = "id,message,from,created_time,like_count,comment_count,is_hidden"

# Per-post insight metrics by Instagram media_product_type. Built against the
# current names: `impressions` was removed for all API versions on 2025-04-21 in
# favour of `views`, and `plays`/`video_views` folded into `views`. Override per
# call with --metric when Meta shifts these again.
MEDIA_METRICS = {
    "REELS": [
        "reach", "views", "likes", "comments", "saved", "shares",
        "total_interactions", "ig_reels_avg_watch_time",
        "ig_reels_video_view_total_time",
    ],
    "STORY": ["reach", "views", "replies", "shares", "total_interactions", "navigation"],
    "FEED": ["reach", "views", "likes", "comments", "saved", "shares", "total_interactions"],
}


def media_metrics(product_type) -> list:
    """The insight metric list for a media's product type, defaulting to FEED."""
    return MEDIA_METRICS.get((product_type or "FEED").upper(), MEDIA_METRICS["FEED"])


def insight_value(item: dict):
    """Pull the scalar from one insight result, across the two response shapes.

    The current `metric_type=total_value` insights return ``total_value.value``;
    the older time-series shape returns the last entry of ``values``.
    """
    total = item.get("total_value")
    if isinstance(total, dict):
        return total.get("value")
    values = item.get("values") or []
    if values:
        return values[-1].get("value")
    return None


def insight_rows(data: list) -> list:
    """Flatten an insights ``data`` array to metric/value/title rows for emit_list."""
    return [
        {"metric": d.get("name"), "value": insight_value(d), "title": d.get("title", "")}
        for d in data
    ]


def appsecret_proof(token: str, app_secret: str) -> str:
    """The Graph appsecret_proof: HMAC-SHA256 of the token, keyed by the app secret."""
    return hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


class MetaError(RuntimeError):
    """A Meta Graph API error, carrying the HTTP status and Graph error code."""

    def __init__(self, message, *, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class MetaSession(HttpSession):
    def __init__(self, token, *, app_secret=None, page_id=None, ig_user_id=None,
                 version=API_VERSION):
        super().__init__(f"{GRAPH}/{version}", timeout=60)
        self.user_token = token
        self.app_secret = app_secret
        self._cfg_page_id = page_id
        self._cfg_ig_user_id = ig_user_id
        self._page = None  # discovered {id, name, access_token, instagram_business_account}
        self.session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # Auth-bearing request core
    # ------------------------------------------------------------------

    def _params(self, token, params):
        p = dict(params or {})
        p["access_token"] = token
        if self.app_secret:
            p["appsecret_proof"] = appsecret_proof(token, self.app_secret)
        return p

    def _call(self, method, path, token, *, params=None):
        return self._request(method, path, params=self._params(token, params))

    def get(self, path, *, params=None, token=None):
        return self._call("GET", path, token or self.page_token, params=params)

    def post(self, path, *, params=None, token=None):
        """Graph writes carry their fields as query params, not a JSON body."""
        return self._call("POST", path, token or self.page_token, params=params)

    def delete(self, path, *, params=None, token=None):
        return self._call("DELETE", path, token or self.page_token, params=params)

    def _raise(self, r) -> None:
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        code = err.get("code")
        msg = err.get("message") or f"HTTP {r.status_code}: {r.text[:200]}"
        if code == 190:
            raise MetaError(
                f"Token rejected (code 190): {msg} The token in [meta] is invalid, "
                f"expired, or the wrong type for this call (see docs/meta.md).",
                status=r.status_code, code=code)
        if code == 210:
            raise MetaError(
                f"Page access token required (code 210): {msg} This call needs a "
                f"Page token; /me/accounts must list the Page, or use a System User "
                f"token with the Page assigned (see docs/meta.md).",
                status=r.status_code, code=code)
        if code in (10, 200, 3, 299):
            raise MetaError(
                f"Permission denied (code {code}): {msg} The token is missing the "
                f"scope or the Page role for this call.",
                status=r.status_code, code=code)
        if code in (4, 17, 32, 613):
            raise MetaError(
                f"Rate limited (code {code}): {msg} Back off and retry later.",
                status=r.status_code, code=code)
        raise MetaError(f"{msg} (code {code})", status=r.status_code, code=code)

    # ------------------------------------------------------------------
    # Lazy discovery: Page token + ids, mirroring CloverSession.merchant_id
    # ------------------------------------------------------------------

    @property
    def page(self) -> dict:
        """The managed Page, resolved once via /me/accounts (user token).

        With a configured page_id, the matching Page is chosen; otherwise the
        first. When /me/accounts is empty (a token without the Pages list, the
        spike's case) the configured page_id and the configured token are used
        directly, so reads still work and writes succeed if the token is itself a
        Page or System User token.
        """
        if self._page is None:
            accounts = self._call(
                "GET", "/me/accounts", self.user_token,
                params={"fields": "id,name,access_token,instagram_business_account"},
            )
            data = accounts.get("data", [])
            if self._cfg_page_id:
                data = [p for p in data if p.get("id") == self._cfg_page_id] or data
            if data:
                self._page = data[0]
            else:
                self._page = {
                    "id": self._cfg_page_id,
                    "name": None,
                    "access_token": self.user_token,
                    "instagram_business_account": (
                        {"id": self._cfg_ig_user_id} if self._cfg_ig_user_id else None
                    ),
                }
        return self._page

    @property
    def page_id(self) -> str:
        pid = self._cfg_page_id or self.page.get("id")
        if not pid:
            raise MetaError(
                "No Page resolved. /me/accounts returned none; set page_id in [meta].")
        return pid

    @property
    def page_token(self) -> str:
        return self.page.get("access_token") or self.user_token

    @property
    def ig_user_id(self) -> str:
        if self._cfg_ig_user_id:
            return self._cfg_ig_user_id
        iba = self.page.get("instagram_business_account")
        if iba and iba.get("id"):
            return iba["id"]
        resp = self._call(
            "GET", f"/{self.page_id}", self.page_token,
            params={"fields": "instagram_business_account"})
        iba = resp.get("instagram_business_account")
        if not iba or not iba.get("id"):
            raise MetaError(
                "No Instagram Business account is linked to this Page. Link it, or "
                "set ig_user_id in [meta].")
        return iba["id"]

    # ------------------------------------------------------------------
    # Cursor pagination over a Graph edge
    # ------------------------------------------------------------------

    def iter_edge(self, path, *, params=None, token=None, max_items=None):
        """Yield rows of an edge, following the ``after`` cursor across pages."""
        token = token or self.page_token
        p = dict(params or {})
        got = 0
        while True:
            resp = self._call("GET", path, token, params=p)
            for item in resp.get("data", []):
                yield item
                got += 1
                if max_items and got >= max_items:
                    return
            paging = resp.get("paging", {})
            after = paging.get("cursors", {}).get("after")
            if not after or not paging.get("next"):
                return
            p["after"] = after
