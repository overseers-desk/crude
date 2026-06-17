"""Airwallex Payouts — transfers: paying money out to a beneficiary.

Read (list/get) plus create over ``/api/v1/transfers``; create moves real money,
so the CLI confirm-gates it. Reads are GET on the collection and ``/{id}``; create
is ``POST /transfers/create``. The list is the usual ``{"items":[...], "has_more"}``
page. A transfer body must carry an idempotency ``request_id``; create_transfer
fills one when the caller's body omits it, so a one-shot CLI create cannot fail on a
missing key, while a caller who wants retry-idempotency supplies their own.
"""

from __future__ import annotations

import uuid


class TransfersAPI:
    def __init__(self, session):
        self.session = session

    def list_transfers(self, *, status=None, from_=None, to=None,
                       all_pages=False, limit=None) -> list:
        """Transfers, page-paged. `from_`/`to` are ISO-8601 UTC instants."""
        params = {"status": status, "from_created_at": from_, "to_created_at": to}
        params = {k: v for k, v in params.items() if v is not None}
        return self.session.paginate("/api/v1/transfers",
                                     params=params or None, all_pages=all_pages, limit=limit)

    def get_transfer(self, transfer_id) -> dict:
        """One transfer by id."""
        data = self.session._get(f"/api/v1/transfers/{transfer_id}")
        return data if isinstance(data, dict) else {}

    def create_transfer(self, body: dict) -> dict:
        """Create a transfer (POST /transfers/create); MOVES REAL MONEY.

        A caller-supplied ``request_id`` wins; otherwise a fresh uuid4 is filled so
        the required idempotency key is always present.
        """
        payload = {"request_id": str(uuid.uuid4()), **(body or {})}
        data = self.session._post("/api/v1/transfers/create", json=payload)
        return data if isinstance(data, dict) else {}
