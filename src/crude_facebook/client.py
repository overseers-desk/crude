"""Facebook Graph API transport: one token, lazy Page discovery.

A FacebookSession carries the configured bearer token and resolves the Page lazily
from ``/me/accounts`` on first use, the direct analogue of how CloverSession
resolves its merchant from ``/v3/merchants/current``. Page writes need a Page
access token, so the session prefers the per-Page token that ``/me/accounts``
returns and falls back to the configured token (with an explicit ``page_id`` config
key) when that edge is empty — the case a Business-Manager-managed Page hits, where
the durable answer is a System User token assigned to the Page.

The Graph API carries auth as the ``access_token`` query parameter and, when the
app enforces it, an ``appsecret_proof`` signature; both ride on every call rather
than on a session header, because the token varies (user token for discovery, Page
token for everything else).
"""

from __future__ import annotations

import hashlib
import hmac

from crude_common import asof
from crude_common.httpapi import HttpSession

GRAPH = "https://graph.facebook.com"
API_VERSION = "v25.0"

# Field sets requested by the read commands.
FB_POST_FIELDS = "id,message,created_time,permalink_url,is_published,full_picture"
FB_PAGE_FIELDS = (
    "id,name,about,category,link,website,phone,emails,followers_count,fan_count"
)
FB_COMMENT_FIELDS = "id,message,from,created_time,like_count,comment_count,is_hidden"


def insight_value(item: dict):
    """Pull the scalar from one insight result, across the two response shapes.

    The `metric_type=total_value` insights return ``total_value.value``; the
    time-series shape returns the last entry of ``values``.
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


class FacebookError(RuntimeError):
    """A Facebook Graph API error, carrying the HTTP status and Graph error code."""

    def __init__(self, message, *, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class FacebookSession(HttpSession):
    def __init__(self, token, *, app_secret=None, page_id=None, version=API_VERSION):
        super().__init__(f"{GRAPH}/{version}", timeout=60)
        self.user_token = token
        self.app_secret = app_secret
        self._cfg_page_id = page_id
        self._page = None  # discovered {id, name, access_token}
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
        """Graph writes carry their fields as query params, not a JSON body.

        POST and DELETE are the Graph write verbs; both refuse under
        WORLD_AS_OF (belt and braces with the do_write gate in the CLI layer).
        """
        asof.guard_write(f"POST {path} on the Graph API")
        return self._call("POST", path, token or self.page_token, params=params)

    def delete(self, path, *, params=None, token=None):
        asof.guard_write(f"DELETE {path} on the Graph API")
        return self._call("DELETE", path, token or self.page_token, params=params)

    def _raise(self, r) -> None:
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        code = err.get("code")
        msg = err.get("message") or f"HTTP {r.status_code}: {r.text[:200]}"
        if code == 190:
            raise FacebookError(
                f"Token rejected (code 190): {msg} The token in [facebook] is invalid, "
                f"expired, or the wrong type for this call (see docs/facebook.md).",
                status=r.status_code, code=code)
        if code == 210:
            raise FacebookError(
                f"Page access token required (code 210): {msg} This call needs a "
                f"Page token; /me/accounts must list the Page, or use a System User "
                f"token with the Page assigned (see docs/facebook.md).",
                status=r.status_code, code=code)
        if code in (10, 200, 3, 299):
            raise FacebookError(
                f"Permission denied (code {code}): {msg} The token is missing the "
                f"scope or the Page role for this call.",
                status=r.status_code, code=code)
        if code in (4, 17, 32, 613):
            raise FacebookError(
                f"Rate limited (code {code}): {msg} Back off and retry later.",
                status=r.status_code, code=code)
        raise FacebookError(f"{msg} (code {code})", status=r.status_code, code=code)

    # ------------------------------------------------------------------
    # Lazy discovery: Page token + id, mirroring CloverSession.merchant_id
    # ------------------------------------------------------------------

    @property
    def page(self) -> dict:
        """The managed Page, resolved once via /me/accounts (user token).

        With a configured page_id, the matching Page is chosen; otherwise the
        first. When /me/accounts is empty (a Business-managed Page) the configured
        page_id and the configured token are used directly, so calls succeed when
        the token is itself a Page or System User token.
        """
        if self._page is None:
            accounts = self._call(
                "GET", "/me/accounts", self.user_token,
                params={"fields": "id,name,access_token"},
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
                }
        return self._page

    @property
    def page_id(self) -> str:
        pid = self._cfg_page_id or self.page.get("id")
        if not pid:
            raise FacebookError(
                "No Page resolved: /me/accounts returned none (common for a "
                "Business-managed Page). Set page_id in [facebook], with a System "
                "User token that has the Page assigned (see docs/facebook.md).")
        return pid

    @property
    def page_token(self) -> str:
        return self.page.get("access_token") or self.user_token

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
