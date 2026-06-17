"""Airwallex transport: a login-refreshing requests session over the REST API.

One AirwallexSession carries the bearer token and the optional connected-account
(x-on-behalf-of) selection. `_request` builds the URL from a single host, injects
the Bearer header, re-logs-in and retries once on a 401, and honours a 429
`Retry-After`. Most list endpoints wrap their collection as
``{"items":[...], "has_more": bool}``, so `paginate` walks page_num/page_size on
`has_more`, and `paginate_cursor` follows the page_before/page_after cursor used by
balance history. A thin `AirwallexClient` facade composes the per-product method
groups.
"""

from __future__ import annotations

import time

import requests

# Default page_num/page_size page; the list commands hint when a full page returns.
PAGE_SIZE = 100

# Bound the 429 back-off so a single call cannot hang the CLI indefinitely.
_MAX_RETRY_AFTER = 60


class AirwallexError(RuntimeError):
    """An Airwallex API error, carrying the HTTP status and the API error code."""

    def __init__(self, message, *, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


class AirwallexAuthError(AirwallexError):
    """Auth failure: a rejected login, or a 401 that survives a re-login."""


def _items(data):
    """Return a list response's records: its ``items`` array, or the data if a list.

    The Airwallex list envelope is ``{"items":[...], "has_more": bool}``; a few
    endpoints return a bare list. Anything else yields [].
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        inner = data.get("items")
        if isinstance(inner, list):
            return inner
    return []


class AirwallexSession:
    def __init__(self, account, client_id, api_key, *, base, on_behalf_of=None, token=None):
        self.account = account
        self.client_id = client_id
        self.api_key = api_key
        self.base = base
        self.on_behalf_of = on_behalf_of
        self.token = token  # durable dict {"token", "expires_at"} or None
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # Token lifecycle (no refresh token; re-login is idempotent)
    # ------------------------------------------------------------------

    def _expired(self) -> bool:
        """True when the bearer is within 60s of (or past) its expiry, or absent."""
        return time.time() >= float((self.token or {}).get("expires_at", 0)) - 60

    def _login(self) -> None:
        from crude_airwallex import auth
        grant = auth.login(self.client_id, self.api_key, base=self.base)
        self.token = auth._durable_token(grant)
        auth.save_token(self.account, self.token)

    def _ensure_token(self) -> None:
        if not self.token or self._expired():
            self._login()

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _headers(self, extra=None) -> dict:
        headers = {"Authorization": f"Bearer {(self.token or {}).get('token')}"}
        if self.on_behalf_of:
            headers["x-on-behalf-of"] = self.on_behalf_of
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _retry_after(r) -> int:
        """Seconds to wait for a 429, from Retry-After, bounded by _MAX_RETRY_AFTER."""
        try:
            wait = int(r.headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            wait = 5
        return max(0, min(wait, _MAX_RETRY_AFTER))

    @staticmethod
    def _error_body(r):
        try:
            body = r.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def _raise(self, r) -> None:
        """Raise AirwallexError/AirwallexAuthError from a failed response.

        Airwallex error bodies carry ``{"code", "message", "source"}``; surface the
        message, falling back to the code, then the status.
        """
        body = self._error_body(r) or {}
        message = body.get("message") or body.get("code") or f"HTTP {r.status_code}"
        code = body.get("code")
        if r.status_code == 401:
            raise AirwallexAuthError(message, status=r.status_code, code=code)
        raise AirwallexError(message, status=r.status_code, code=code)

    def _request(self, method, path, *, params=None, json=None, headers=None, _retry=True):
        """Issue one request, re-logging-in on a 401 and backing off a 429 once."""
        self._ensure_token()
        r = self.session.request(method, self.base + path, params=params, json=json,
                                 headers=self._headers(headers))
        if r.status_code == 401 and _retry:
            self._login()
            return self._request(method, path, params=params, json=json,
                                 headers=headers, _retry=False)
        if r.status_code == 429 and _retry:
            time.sleep(self._retry_after(r))
            return self._request(method, path, params=params, json=json,
                                 headers=headers, _retry=False)
        if not r.ok:
            self._raise(r)
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return r.content

    def _get(self, path, *, params=None, headers=None):
        return self._request("GET", path, params=params, headers=headers)

    def _post(self, path, *, json=None, params=None, headers=None):
        return self._request("POST", path, json=json, params=params, headers=headers)

    def _put(self, path, *, json=None, params=None, headers=None):
        return self._request("PUT", path, json=json, params=params, headers=headers)

    def _delete(self, path, *, params=None, headers=None):
        return self._request("DELETE", path, params=params, headers=headers)

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    def paginate(self, path, *, params=None, page_size=PAGE_SIZE, limit=None, all_pages=True,
                 headers=None):
        """Page a collection via page_num/page_size, stopping on ``has_more`` False.

        `all_pages` walks every page; False fetches only the first. `limit` caps the
        total records, paging as needed and then truncating. page_num is 0-based.
        `headers` are sent on every page request (e.g. the FX endpoints' x-api-version).
        """
        results = []
        base = dict(params or {})
        base.setdefault("page_size", page_size)
        page = 0
        while True:
            base["page_num"] = page
            data = self._get(path, params=base, headers=headers)
            chunk = _items(data)
            if not chunk:
                break
            results.extend(chunk)
            if limit is not None and len(results) >= limit:
                break
            has_more = bool(data.get("has_more")) if isinstance(data, dict) else False
            if not has_more:
                break
            if limit is None and not all_pages:
                break
            page += 1
        return results[:limit] if limit is not None else results

    def paginate_cursor(self, path, *, params=None, limit=None):
        """Page a cursor collection (balance history) via page_after/has_more.

        The response carries the next cursor in ``page_after``; follow it until
        ``has_more`` is False or no cursor is returned.
        """
        results = []
        base = dict(params or {})
        while True:
            data = self._get(path, params=base)
            chunk = _items(data)
            if not chunk:
                break
            results.extend(chunk)
            if limit is not None and len(results) >= limit:
                break
            has_more = bool(data.get("has_more")) if isinstance(data, dict) else False
            cursor = data.get("page_after") if isinstance(data, dict) else None
            if not has_more or not cursor:
                break
            base["page_after"] = cursor
        return results[:limit] if limit is not None else results


class AirwallexClient:
    """Facade composing the per-product method groups over one AirwallexSession.

    Composes the core treasury reads (account, balances, financial transactions) and
    the Payouts group (beneficiaries, transfers, FX rates and conversions). Payments
    Acceptance and Issuing are added as those modules land.
    """

    def __init__(self, session: AirwallexSession):
        from crude_airwallex.core import CoreAPI
        from crude_airwallex.beneficiaries import BeneficiariesAPI
        from crude_airwallex.transfers import TransfersAPI
        from crude_airwallex.fx import FxAPI
        self.session = session
        self.core = CoreAPI(session)
        self.beneficiaries = BeneficiariesAPI(session)
        self.transfers = TransfersAPI(session)
        self.fx = FxAPI(session)
