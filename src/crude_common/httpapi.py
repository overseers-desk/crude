"""Shared HTTP transport for the requests-based crude site clients.

HttpSession owns the request/retry control flow common to the homogeneous REST
clients (airwallex, atdw, clover, deputy): build the URL from a base, issue the
request, retry once on a 401 when the site refreshes its credential, back off a
429 Retry-After, raise on error, and parse the body. Per-site divergence rides
on the `_on_401` and `_raise` hooks (with `_ensure_token`/`_build_url`/
`_auth_headers`/`_parse` defaulting to the common case). The rezdy and xero
clients keep their own transport (query-param auth; product-routed URLs), and
skal (Odoo JSON-RPC) and sonas (Meteor DDP) are not HTTP.
"""

from __future__ import annotations

import time

import requests


class HttpSession:
    # 429 Retry-After bounds (seconds); clover narrows these.
    _MAX_RETRY_AFTER = 60
    _RETRY_AFTER_DEFAULT = 5

    def __init__(self, base_url, *, session=None, timeout=None):
        self.base_url = base_url
        self.session = session or requests.Session()
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Hooks — override per site; the defaults cover the common case.
    # ------------------------------------------------------------------

    def _on_401(self, r) -> bool:
        """Refresh the credential and return True to retry once; default: no refresh."""
        return False

    def _ensure_token(self) -> None:
        """Pre-request credential check; default: nothing."""

    def _raise(self, r) -> None:
        """Raise a site error from a failed response; default: requests' own."""
        r.raise_for_status()

    def _build_url(self, path) -> str:
        return self.base_url + path

    def _auth_headers(self, extra):
        """Per-request headers. Default: the caller's, with auth carried on the
        session. Sites that mint a per-call auth header (a rotating bearer) override."""
        return extra

    def _parse(self, r):
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return r.content

    @classmethod
    def _retry_after(cls, r) -> int:
        """Seconds to wait for a 429, from Retry-After, bounded by the class limits."""
        try:
            wait = int(r.headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            wait = cls._RETRY_AFTER_DEFAULT
        return max(0, min(wait, cls._MAX_RETRY_AFTER))

    # ------------------------------------------------------------------
    # The one request path: refresh-on-401, back-off-on-429, raise, parse.
    # ------------------------------------------------------------------

    def _request(self, method, path, *, params=None, json=None, headers=None, _retry=True):
        self._ensure_token()
        url = self._build_url(path)
        kw = {"params": params, "json": json, "headers": self._auth_headers(headers)}
        if self.timeout is not None:
            kw["timeout"] = self.timeout
        r = self.session.request(method, url, **kw)
        if r.status_code == 401 and _retry and self._on_401(r):
            return self._request(method, path, params=params, json=json,
                                 headers=headers, _retry=False)
        if r.status_code == 429 and _retry:
            time.sleep(self._retry_after(r))
            return self._request(method, path, params=params, json=json,
                                 headers=headers, _retry=False)
        if not r.ok:
            self._raise(r)
        return self._parse(r)

    def _get(self, path, *, params=None, headers=None):
        return self._request("GET", path, params=params, headers=headers)

    def _post(self, path, *, json=None, params=None, headers=None):
        return self._request("POST", path, json=json, params=params, headers=headers)

    def _put(self, path, *, json=None, params=None, headers=None):
        return self._request("PUT", path, json=json, params=params, headers=headers)

    def _patch(self, path, *, json=None, params=None, headers=None):
        return self._request("PATCH", path, json=json, params=params, headers=headers)

    def _delete(self, path, *, params=None, headers=None):
        return self._request("DELETE", path, params=params, headers=headers)
