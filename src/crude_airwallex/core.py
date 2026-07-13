"""Airwallex core treasury reads: account, balances, financial transactions.

The 'get transaction info' surface, all read-only: the connected account, current
and historical balances, and the financial-transactions ledger with
date/currency/status filters. Timestamp fields are rendered in local time by the
CLI layer (crude_common.localtime), not here, so these methods return the API
records unchanged.
"""

from __future__ import annotations

from crude_common import asof
from crude_airwallex.client import _items


class CoreAPI:
    def __init__(self, session):
        self.session = session

    def get_account(self) -> dict:
        """The connected Airwallex account (settings and details)."""
        data = self.session._get("/api/v1/account")
        return data if isinstance(data, dict) else {}

    def list_current_balances(self) -> list:
        """Current balance per held currency."""
        return _items(self.session._get("/api/v1/balances/current"))

    def list_balance_history(self, *, currency=None, from_=None, to=None, limit=None) -> list:
        """Balance-affecting entries (the ledger behind the balance), cursor-paged.

        `from_`/`to` are ISO-8601 UTC instants (the CLI converts local dates).
        Under WORLD_AS_OF the upper bound is clamped server-side and entries are
        post-filtered on ``posted_at`` (the ledger's own instant).
        """
        asof.check_window_start(from_)
        params = {"currency": currency, "from_created_at": from_,
                  "to_created_at": asof.clamp_upper_iso(to)}
        params = {k: v for k, v in params.items() if v is not None}
        items = self.session.paginate_cursor("/api/v1/balances/history",
                                             params=params or None, limit=limit)
        return asof.bound_records(items, "posted_at", what="balance entry")

    def list_financial_transactions(self, *, currency=None, status=None, from_=None,
                                    to=None, all_pages=False, limit=None) -> list:
        """The financial-transactions ledger, filtered and page-paged.

        Under WORLD_AS_OF ``to_created_at`` is clamped server-side; a ledger row
        settled after the cutoff is served in its settled state, flagged.
        """
        asof.check_window_start(from_)
        params = {"currency": currency, "status": status,
                  "from_created_at": from_,
                  "to_created_at": asof.clamp_upper_iso(to)}
        params = {k: v for k, v in params.items() if v is not None}
        items = self.session.paginate("/api/v1/financial_transactions",
                                      params=params or None, all_pages=all_pages, limit=limit)
        return asof.bound_records(items, "createdAt", "settledAt", what="transaction")

    def get_financial_transaction(self, txn_id) -> dict:
        """One financial transaction by id."""
        data = self.session._get(f"/api/v1/financial_transactions/{txn_id}")
        return data if isinstance(data, dict) else {}
