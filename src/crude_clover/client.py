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

import requests

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


class CloverSession:
    def __init__(self, token, *, base=BASE):
        self.token = token
        self.base = base
        self._merchant_id = None
        self.session = requests.Session()
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
        r = self.session.get(self.base + path, params=params, timeout=60)
        if r.status_code == 401:
            raise CloverError(
                "Clover rejected the token (401). Check [clover] api_token is an AP "
                "production token with Read on Merchant, Inventory, Orders, Payments.",
                status=401,
            )
        if not r.ok:
            raise CloverError(f"HTTP {r.status_code}: {r.text[:200]}", status=r.status_code)
        return r.json()

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
    """Facade composing the orders and catalog method groups over one session."""

    def __init__(self, session: CloverSession):
        from crude_clover.orders import OrdersAPI
        from crude_clover.catalog import CatalogAPI

        self.session = session
        self.orders = OrdersAPI(session)
        self.catalog = CatalogAPI(session)
