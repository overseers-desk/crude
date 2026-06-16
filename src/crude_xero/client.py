"""Xero transport: a token-refreshing requests session over the seven product APIs.

One `XeroSession` carries the OAuth state (Bearer access token plus the durable,
rotating refresh token) and the selected tenant. `_request` carries every verb
and product: it builds the URL from a product base-path table, injects the
`xero-tenant-id` header, refreshes the token on expiry or a 401, and honours a
429 `Retry-After`. The Accounting API wraps a collection under its plural
resource key (``{"Invoices":[...],"Status":"OK"}``), so `_extract_list` pulls the
single list-valued key by position, mirroring rezdy's `_payload`. A thin
`XeroClient` facade composes the per-product method groups.
"""

from __future__ import annotations

import json as _json
import time

import requests

PRODUCT_BASES = {
    "accounting": "https://api.xero.com/api.xro/2.0/",
    "payroll_au": "https://api.xero.com/payroll.xro/2.0/",
    "files": "https://api.xero.com/files.xro/1.0/",
    "assets": "https://api.xero.com/assets.xro/1.0/",
    "projects": "https://api.xero.com/projects.xro/2.0/",
    "bankfeeds": "https://api.xero.com/bankfeeds.xro/1.0/",
    "finance": "https://api.xero.com/finance.xro/1.0/",
}

CONNECTIONS_URL = "https://api.xero.com/connections"

# Accounting's `page` param caps each page at this many records; a full page is
# the signal that more likely exist (the list commands hint on it).
PAGE_SIZE = 100

# Bound the 429 back-off so a single call cannot hang the CLI indefinitely.
_MAX_RETRY_AFTER = 60


class XeroError(RuntimeError):
    """A Xero API error, carrying the HTTP status, message, and rate-limit problem."""

    def __init__(self, message, *, status=None, problem=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.problem = problem


class XeroAuthError(XeroError):
    """Auth failure (e.g. invalid_grant); the user must run `crude-xero auth`."""


def _extract_list(data):
    """Return an Accounting response's list payload: the one list-valued key.

    Accounting wraps a collection as ``{"Invoices":[...],"Status":"OK","Id":...}``;
    pull the single list value, ignoring the status/envelope scalars, mirroring
    rezdy's `_payload`. Falls back to [] when no list is present.
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    lists = [v for v in data.values() if isinstance(v, list)]
    if len(lists) == 1:
        return lists[0]
    for v in lists:
        if v:
            return v
    return []


class XeroSession:
    def __init__(self, account, client_id, client_secret, tokens: dict, tenant_id=None):
        self.account = account
        self.client_id = client_id
        self.client_secret = client_secret
        self.tokens = tokens
        self.tenant_id = tenant_id
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # Token lifecycle
    # ------------------------------------------------------------------

    def _expired(self) -> bool:
        """True when the access token is within 60s of (or past) its expiry."""
        return time.time() >= float(self.tokens.get("expires_at", 0)) - 60

    def _refresh(self) -> None:
        """Rotate the token under the file lock, never clobbering on failure.

        Re-reads the side file first: if another process already rotated to a
        still-valid token, adopt it instead of refreshing again (the rotated
        refresh token is single-use). Otherwise refresh; on success persist and
        update in memory, on failure leave the stored token untouched.
        """
        from crude_xero import auth
        with auth.token_lock(self.account):
            fresh = auth.load_tokens(self.account, {})
            if fresh and float(fresh.get("expires_at", 0)) - 60 > time.time():
                self.tokens = fresh
                return
            current = fresh or self.tokens
            refresh_token = current.get("refresh_token")
            if not refresh_token:
                raise XeroAuthError("No refresh token available; run `crude-xero auth`.")
            data = auth.refresh_token_grant(self.client_id, self.client_secret, refresh_token)
            now = time.time()
            new_tokens = {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", refresh_token),
                "obtained_at": now,
                "expires_at": now + int(data.get("expires_in", 1800)),
                "scope": data.get("scope", current.get("scope", "")),
            }
            auth.save_tokens(self.account, new_tokens)
            self.tokens = new_tokens

    def _ensure_token(self) -> None:
        if self._expired():
            self._refresh()

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _headers(self, extra=None) -> dict:
        headers = {"Authorization": f"Bearer {self.tokens.get('access_token')}"}
        if self.tenant_id:
            headers["xero-tenant-id"] = self.tenant_id
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
    def _xero_message(r) -> str:
        """The human message from a Xero error body, validation detail preferred.

        On a validation failure Xero gives a generic top-level ``Message`` ("A
        validation exception occurred") and the actionable detail in nested
        ``Elements[].ValidationErrors[].Message``; surface the nested detail when
        present, else the OAuth ``error``, else the top-level ``Message``.
        """
        try:
            body = r.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return f"HTTP {r.status_code}"
        errors = []
        for el in body.get("Elements") or []:
            for ve in el.get("ValidationErrors") or []:
                if ve.get("Message"):
                    errors.append(ve["Message"])
        if errors:
            return "; ".join(errors)
        if body.get("error"):
            return body.get("error_description") or body.get("error") or f"HTTP {r.status_code}"
        return body.get("Message") or f"HTTP {r.status_code}"

    @classmethod
    def _raise_for_xero(cls, r) -> None:
        """Raise XeroError/XeroAuthError from a failed response's Xero error shape."""
        message = cls._xero_message(r)
        problem = r.headers.get("X-Rate-Limit-Problem")
        if r.status_code == 401:
            raise XeroAuthError(message, status=r.status_code, problem=problem)
        raise XeroError(message, status=r.status_code, problem=problem)

    def _raise_permission(self, r) -> None:
        """Raise a scope-aware error for a forbidden operation.

        A 403, or a 401 that persists after a successful refresh, means the token
        is valid but the operation is not permitted — most often a missing OAuth
        scope. Re-auth alone does not help; the scope must first be added to the
        config. Name the scopes actually granted so the gap is visible.
        """
        detail = self._xero_message(r)
        granted = self.tokens.get("scope") or "(unknown)"
        raise XeroError(
            f"{detail} (HTTP {r.status_code}). Xero refused this operation; the usual "
            f"cause is a missing OAuth scope. Granted scopes: {granted}. Add the scope "
            f"this resource needs to [xero] scopes in config.toml, then run `crude-xero auth`.",
            status=r.status_code,
            problem=r.headers.get("X-Rate-Limit-Problem"),
        )

    def _request(self, method, product, path, *, params=None, json=None,
                 headers=None, accept=None, data=None, _retry=True):
        """Issue one request to a product base path, refreshing/retrying as needed.

        Returns parsed JSON, raw bytes when `accept` is a non-JSON type (PDF,
        octet-stream), or {} on an empty body. A raw `data` body with an explicit
        Content-Type header carries attachment uploads.
        """
        self._ensure_token()
        url = PRODUCT_BASES[product] + path.lstrip("/")
        extra = dict(headers or {})
        if accept:
            extra["Accept"] = accept
        r = self.session.request(method, url, params=params, json=json,
                                 data=data, headers=self._headers(extra))
        if r.status_code == 401 and _retry:
            self._refresh()
            return self._request(method, product, path, params=params, json=json,
                                 headers=headers, accept=accept, data=data, _retry=False)
        if r.status_code == 429 and _retry:
            time.sleep(self._retry_after(r))
            return self._request(method, product, path, params=params, json=json,
                                 headers=headers, accept=accept, data=data, _retry=False)
        if r.status_code in (401, 403):
            # A 401 reaching here is post-refresh (the first 401 already refreshed
            # and retried), so the token is valid; a 403 is outright forbidden.
            # Either is a permissions/scope problem, not token expiry.
            self._raise_permission(r)
        if not r.ok:
            self._raise_for_xero(r)
        if accept and "json" not in accept.lower():
            return r.content
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return r.content

    # ------------------------------------------------------------------
    # Thin verb wrappers
    # ------------------------------------------------------------------

    def _get(self, product, path, *, params=None, headers=None, accept=None):
        return self._request("GET", product, path, params=params, headers=headers, accept=accept)

    def _post(self, product, path, *, json=None, params=None, headers=None):
        return self._request("POST", product, path, json=json, params=params, headers=headers)

    def _put(self, product, path, *, json=None, params=None, headers=None):
        return self._request("PUT", product, path, json=json, params=params, headers=headers)

    def _delete(self, product, path, *, params=None, headers=None):
        return self._request("DELETE", product, path, params=params, headers=headers)

    def _put_raw(self, product, path, *, data, content_type):
        """PUT a raw byte body (attachment upload), not JSON."""
        return self._request("PUT", product, path, data=data,
                             headers={"Content-Type": content_type})

    # ------------------------------------------------------------------
    # Cross-product helpers
    # ------------------------------------------------------------------

    def connections(self) -> list:
        """List the tenants this token can reach (Bearer only, no tenant header)."""
        self._ensure_token()
        bearer = {"Authorization": f"Bearer {self.tokens.get('access_token')}",
                  "Accept": "application/json"}
        r = self.session.get(CONNECTIONS_URL, headers=bearer)
        if r.status_code == 401:
            self._refresh()
            bearer["Authorization"] = f"Bearer {self.tokens.get('access_token')}"
            r = self.session.get(CONNECTIONS_URL, headers=bearer)
        if not r.ok:
            self._raise_for_xero(r)
        return r.json() if r.content else []

    def paginate(self, product, path, *, params=None, page_size=PAGE_SIZE,
                 limit=None, all_pages=True) -> list:
        """Page an Accounting collection via the `page` param.

        `all_pages` walks every page to the end; False fetches only the first
        page (for the endpoints Xero does not page, that one response is the
        whole collection anyway). `limit` caps the total records, paging across
        as many pages as needed and then truncating; it overrides the
        single-page stop.

        Several Accounting endpoints (Accounts, Items, TaxRates, ...) ignore
        `page` and return the whole collection on every request; asking for the
        next page would loop forever on the same data. The walk therefore stops
        as soon as a page repeats the previous page's first record: a genuinely
        paged endpoint never does that, an unpaged one always does.
        """
        results = []
        page = 1
        base = dict(params or {})
        prev_first = None
        while True:
            base["page"] = page
            chunk = _extract_list(self._get(product, path, params=base))
            if not chunk:
                break
            first = _json.dumps(chunk[0], sort_keys=True, default=str)
            if first == prev_first:
                break
            prev_first = first
            results.extend(chunk)
            if limit is not None and len(results) >= limit:
                break
            if len(chunk) < page_size:
                break
            if limit is None and not all_pages:
                break
            page += 1
        return results[:limit] if limit is not None else results


class XeroClient:
    """Facade composing the per-product method groups over one XeroSession."""

    def __init__(self, session: XeroSession):
        from crude_xero.accounting import AccountingAPI
        self.session = session
        self.accounting = AccountingAPI(session)
        # Later phases add: self.files, self.assets, self.projects (Phase 2),
        # self.payroll (Phase 3), self.bankfeeds, self.finance (Phase 4).
