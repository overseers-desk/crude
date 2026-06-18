"""Clover transport: a Bearer-token requests session over the AP REST API.

One CloverSession carries the static API token (issued once from the AP
production dashboard) and resolves the merchant id lazily from
``/v3/merchants/current`` on first use, so the token is the single source of
truth for which merchant is read and the id is never stored in config. Clover's
list endpoints wrap their rows as ``{"elements": [...]}`` and page by
``limit``/``offset``; ``iter_elements`` walks that envelope. A thin CloverClient
facade composes the orders and catalog method groups.

Unlike crude-airwallex there is no OAuth exchange or token refresh: the token is
durable, so this module has no auth.py companion.
"""

from __future__ import annotations

import sys
import time

from crude_common.httpapi import HttpSession

# AP production base. EU/US/sandbox bases reject AP tokens with HTTP 401; only
# the dashboard region that issued the token matters, not the caller's location.
BASE = "https://api.ap.clover.com"

# Clover's per-page cap on list endpoints, and its hard ceiling on offset: a
# single filtered range can page through at most 10000 rows. Orders splits the
# time range to stay under it (see orders.py); the catalog is far smaller.
PAGE = 100
OFFSET_CAP = 10000


class CloverError(RuntimeError):
    """A Clover API error, carrying the HTTP status."""

    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status
        self.message = message


class CloverSession(HttpSession):
    # Clover's 429 budget is tighter than the shared default (cap 30s, default 2s).
    _MAX_RETRY_AFTER = 30
    _RETRY_AFTER_DEFAULT = 2

    def __init__(self, token, *, base=BASE):
        super().__init__(base, timeout=60)
        self.token = token
        self._merchant_id = None
        self.session.headers.update(
            {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        )

    @property
    def merchant_id(self) -> str:
        """The token's merchant, resolved once via /v3/merchants/current."""
        if self._merchant_id is None:
            self._merchant_id = self.get("/v3/merchants/current")["id"]
        return self._merchant_id

    def get(self, path, *, params=None) -> dict:
        return self._get(path, params=params)

    def post(self, path, *, json=None) -> dict:
        """POST a body. Clover creates via POST to the collection and updates via
        POST to the element, so this serves both create and update."""
        return self._post(path, json=json)

    def delete(self, path) -> dict:
        return self._delete(path)

    def _raise(self, r) -> None:
        if r.status_code in (401, 403):
            raise CloverError(
                f"Clover denied the request ({r.status_code}). The token lacks the scope "
                f"for {r.request.method} {r.request.path_url}. Enable it in the AP dashboard "
                f"(Setup -> API Tokens).",
                status=r.status_code,
            )
        raise CloverError(f"HTTP {r.status_code}: {r.text[:200]}", status=r.status_code)

    def probe(self, method, path, *, params=None, json=None) -> int:
        """The HTTP status of a request, without raising. For the scope probe:
        a write probe sends a body Clover rejects on content (4xx) so nothing is
        created, distinguishing a missing scope (401/403) from a bad body (400).
        Retries once on a 429 so rate-limiting is not mistaken for a scope block."""
        for attempt in (0, 1):
            r = self.session.request(method, self.base_url + path, params=params, json=json, timeout=30)
            if r.status_code == 429 and attempt == 0:
                time.sleep(min(int(r.headers.get("Retry-After") or 2), 30))
                continue
            return r.status_code
        return r.status_code

    def iter_elements(self, path, *, expand=None, filters=None):
        """Yield every ``elements`` row of a list endpoint, paging by offset.

        For collections without a time filter (the catalog). Warns and stops at
        the 10000-offset ceiling; orders, which can exceed it, split the range
        instead (see OrdersAPI.iter_orders).
        """
        offset = 0
        while True:
            params = [("limit", PAGE), ("offset", offset)]
            if expand:
                params.append(("expand", expand))
            for f in filters or []:
                params.append(("filter", f))
            elems = self.get(path, params=params).get("elements", [])
            yield from elems
            if len(elems) < PAGE:
                return
            offset += PAGE
            if offset >= OFFSET_CAP:
                print(
                    f"WARNING: {path} exceeded {OFFSET_CAP} rows; results truncated.",
                    file=sys.stderr,
                )
                return


class CloverClient:
    """Facade composing the orders, catalog, and generic resource groups."""

    def __init__(self, session: CloverSession):
        from crude_clover.orders import OrdersAPI
        from crude_clover.catalog import CatalogAPI
        from crude_clover.resources import ResourceAPI

        self.session = session
        self.orders = OrdersAPI(session)
        self.catalog = CatalogAPI(session)
        self.resources = ResourceAPI(session)
