"""Xero Bank Feeds API (bankfeeds.xro/1.0) method group over a XeroSession.

One method group for the BankFeeds product: the feed connections that bind a
financial-institution account to a Xero bank account, and the statements pushed
onto those feeds. Like Assets and Projects, a collection comes back wrapped as
``{"pagination": {...}, "items": [...]}`` — unwrap the ``items`` key (BankFeeds's
own shape, not Accounting's single-plural-key wrap). Two BankFeeds shapes are
unusual and load-bearing here: a write posts a batch under an ``items`` wrapper
(``{"items": [{...}]}``), and a feed connection is removed by POSTing a delete
request to ``FeedConnections/DeleteRequests`` rather than issuing an HTTP DELETE.

BankFeeds is gated to partner applications, so it is not live-testable from a
standard developer app; this group is built to the documented contract and its
tests are mock-only.
"""

from __future__ import annotations

BASE = "bankfeeds"


class BankFeedsAPI:
    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _items(data):
        """Unwrap the records from the ``{"pagination":..., "items":[...]}`` envelope.

        BankFeeds keeps a collection's records under ``items``, with the paging
        metadata beside them under ``pagination`` — the same shape as Assets and
        Projects, distinct from Accounting's single-plural-key wrap. Pull
        ``items``, tolerating a bare list and odd shapes (falls back to []).
        """
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                return items
        if isinstance(data, list):
            return data
        return []

    def _list(self, path, *, page=None, page_size=None):
        """GET a BankFeeds collection (one page) and return its ``items``.

        BankFeeds pages via its own ``page``/``pageSize`` query params; pass them
        through, dropping the unset ones.
        """
        params = {"page": page, "pageSize": page_size}
        params = {k: v for k, v in params.items() if v is not None}
        return self._items(self.session._get(BASE, path, params=params or None))

    # ------------------------------------------------------------------
    # Feed connections (link an FI account to a Xero bank account)
    # ------------------------------------------------------------------

    def list_feed_connections(self, page=None, page_size=None):
        return self._list("FeedConnections", page=page, page_size=page_size)

    def get_feed_connection(self, feed_connection_id):
        return self.session._get(BASE, f"FeedConnections/{feed_connection_id}")

    def create_feed_connections(self, body):
        """Create one or more feed connections (POST a ``{"items":[{...}]}`` batch)."""
        return self.session._post(BASE, "FeedConnections", json=body)

    def delete_feed_connections(self, body):
        """Delete feed connections by POSTing a delete-request batch.

        BankFeeds exposes no HTTP DELETE for feed connections; a connection is
        closed by POSTing a ``{"items":[{...}]}`` batch of delete requests to the
        ``FeedConnections/DeleteRequests`` sub-resource. Implemented as a POST
        accordingly, not a ``_delete``.
        """
        return self.session._post(BASE, "FeedConnections/DeleteRequests", json=body)

    # ------------------------------------------------------------------
    # Statements (transactions pushed onto a feed)
    # ------------------------------------------------------------------

    def list_statements(self, page=None, page_size=None):
        return self._list("Statements", page=page, page_size=page_size)

    def get_statement(self, statement_id):
        return self.session._get(BASE, f"Statements/{statement_id}")

    def create_statements(self, body):
        """Create one or more statements (POST a ``{"items":[{...}]}`` batch)."""
        return self.session._post(BASE, "Statements", json=body)
