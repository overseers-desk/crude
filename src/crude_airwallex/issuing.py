"""Airwallex Issuing: the cards a business spins up for its own spending.

The `issuing` product group, under ``/api/v1/issuing/``: cards (a provisioned
spending instrument), cardholders (the person a card is issued to), authorizations
(a live card-auth attempt, read-only), and transactions (a settled card
transaction, read-only). Reads are GET on the collection and ``/{id}``.

Two write-path shapes appear here, and they differ from the Payouts ``/update/{id}``
convention: a new object is created at ``/cards|cardholders/create`` (POST), while an
update targets the resource-then-action path ``/cards|cardholders/{id}/update`` (POST,
the same shape the payment-intent action verbs use). A card or cardholder create
carries an idempotency ``request_id``; `_with_request_id` fills a uuid4 when the
caller omits one. An update is a *partial* body POSTed straight through, not a
read-merge-write: an issued card round-trips read-only fields (the masked
``card_number``, ``created_at``) the update endpoint rejects, so the caller sends only
the fields to change (e.g. ``{"card_status": "INACTIVE"}`` to freeze).

NEVER call a full-PAN / sensitive-details endpoint here: the standard card object
already carries a masked number, and that is all crude ever surfaces.

Field names are snake_case (verified live). The issuing list filters use
``from_created_date`` / ``to_created_date`` (not the ``_at`` form the
financial-transactions and pa endpoints take), defaulting to a 30-day window when
omitted. Timestamp fields are localized by the CLI layer.
"""

from __future__ import annotations

import uuid

_ISSUING = "/api/v1/issuing"


def _with_request_id(body: dict) -> dict:
    """A copy of the body with an idempotency ``request_id`` filled if absent."""
    return {"request_id": str(uuid.uuid4()), **(body or {})}


def _date_params(status=None, from_=None, to=None, **extra) -> dict:
    """The shared issuing list filters, Nones dropped.

    `from_`/`to` map to the issuing ``from_created_date`` / ``to_created_date`` query
    params (the ``_date`` form, unlike core/pa's ``_at``). `extra` carries the
    per-resource filters (e.g. ``cardholder_id``, ``card_id``).
    """
    params = {"status": status, "from_created_date": from_, "to_created_date": to, **extra}
    return {k: v for k, v in params.items() if v is not None}


class IssuingAPI:
    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------------
    # cards (a provisioned spending instrument; create/update gated by the CLI)
    # ------------------------------------------------------------------

    def list_cards(self, *, status=None, cardholder_id=None, from_=None, to=None,
                   all_pages=False, limit=None) -> list:
        """Issued cards, page-paged. `from_`/`to` are ISO-8601 UTC instants."""
        params = _date_params(status, from_, to, cardholder_id=cardholder_id)
        return self.session.paginate(f"{_ISSUING}/cards",
                                     params=params or None, all_pages=all_pages, limit=limit)

    def get_card(self, card_id) -> dict:
        """One issued card by id (standard object: the card number is masked)."""
        data = self.session._get(f"{_ISSUING}/cards/{card_id}")
        return data if isinstance(data, dict) else {}

    def create_card(self, body: dict) -> dict:
        """Provision a card (POST .../cards/create); fills an idempotency request_id."""
        data = self.session._post(f"{_ISSUING}/cards/create", json=_with_request_id(body))
        return data if isinstance(data, dict) else {}

    def update_card(self, card_id, body: dict) -> dict:
        """Update a card (POST .../cards/{id}/update) with a partial body.

        A status change freezes (``INACTIVE``) / cancels (``CLOSED``) or re-activates
        the card; limits/controls ride in ``authorization_controls``. The body is sent
        through verbatim (only the fields to change), not a fetched-and-merged object.
        """
        data = self.session._post(f"{_ISSUING}/cards/{card_id}/update", json=body or {})
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # cardholders (the person a card is issued to)
    # ------------------------------------------------------------------

    def list_cardholders(self, *, status=None, from_=None, to=None,
                         all_pages=False, limit=None) -> list:
        """Cardholders, page-paged. `from_`/`to` are ISO-8601 UTC instants."""
        params = _date_params(status, from_, to)
        return self.session.paginate(f"{_ISSUING}/cardholders",
                                     params=params or None, all_pages=all_pages, limit=limit)

    def get_cardholder(self, cardholder_id) -> dict:
        """One cardholder by id."""
        data = self.session._get(f"{_ISSUING}/cardholders/{cardholder_id}")
        return data if isinstance(data, dict) else {}

    def create_cardholder(self, body: dict) -> dict:
        """Create a cardholder (POST .../cardholders/create); fills a request_id."""
        data = self.session._post(f"{_ISSUING}/cardholders/create", json=_with_request_id(body))
        return data if isinstance(data, dict) else {}

    def update_cardholder(self, cardholder_id, body: dict) -> dict:
        """Update a cardholder (POST .../cardholders/{id}/update) with a partial body."""
        data = self.session._post(f"{_ISSUING}/cardholders/{cardholder_id}/update", json=body or {})
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # authorizations (a live card-auth attempt; read-only)
    # ------------------------------------------------------------------

    def list_authorizations(self, *, status=None, card_id=None, from_=None, to=None,
                           all_pages=False, limit=None) -> list:
        """Card authorizations, page-paged. `from_`/`to` are ISO-8601 UTC instants."""
        params = _date_params(status, from_, to, card_id=card_id)
        return self.session.paginate(f"{_ISSUING}/authorizations",
                                     params=params or None, all_pages=all_pages, limit=limit)

    def get_authorization(self, authorization_id) -> dict:
        """One authorization by id."""
        data = self.session._get(f"{_ISSUING}/authorizations/{authorization_id}")
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # transactions (a settled card transaction; read-only)
    # ------------------------------------------------------------------

    def list_transactions(self, *, status=None, card_id=None, from_=None, to=None,
                         all_pages=False, limit=None) -> list:
        """Settled card transactions, page-paged. `from_`/`to` are ISO-8601 UTC instants."""
        params = _date_params(status, from_, to, card_id=card_id)
        return self.session.paginate(f"{_ISSUING}/transactions",
                                     params=params or None, all_pages=all_pages, limit=limit)

    def get_transaction(self, transaction_id) -> dict:
        """One card transaction by id."""
        data = self.session._get(f"{_ISSUING}/transactions/{transaction_id}")
        return data if isinstance(data, dict) else {}
