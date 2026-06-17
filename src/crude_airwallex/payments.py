"""Airwallex Payments Acceptance — the inbound side: collecting money from shoppers.

The `pa` product group, under ``/api/v1/pa/``: payment intents (a request to
collect a payment, with confirm/capture/cancel money verbs), refunds, customers (a
saved shopper, no money), payment consents (a saved mandate, read-only), and payment
links (a hosted page that collects a payment). Reads are GET on the collection and
``/{id}``; the writes follow the account's POST-with-suffix convention — ``/create``
for new objects, ``/update/{id}`` / ``/delete/{id}`` for customers, and the payment
intent's own ``/{id}/confirm|capture|cancel`` action paths.

The money verbs (intent create/confirm/capture/cancel, refund create, link create)
carry an idempotency ``request_id``; `_with_request_id` fills a uuid4 when the
caller's body omits one, so a one-shot CLI write cannot fail on a missing key, while
a caller wanting retry-idempotency supplies their own. Customers move no money and
pass their body through verbatim. Timestamp fields are localized by the CLI layer.
"""

from __future__ import annotations

import uuid

_PA = "/api/v1/pa"


def _with_request_id(body: dict) -> dict:
    """A copy of the body with an idempotency ``request_id`` filled if absent."""
    return {"request_id": str(uuid.uuid4()), **(body or {})}


def _date_params(status=None, from_=None, to=None) -> dict:
    """The shared list filters, Nones dropped (from_/to are ISO-8601 UTC instants)."""
    params = {"status": status, "from_created_at": from_, "to_created_at": to}
    return {k: v for k, v in params.items() if v is not None}


class PaymentsAPI:
    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------------
    # payment_intents (request a payment; confirm/capture/cancel money verbs)
    # ------------------------------------------------------------------

    def list_payment_intents(self, *, status=None, from_=None, to=None,
                             all_pages=False, limit=None) -> list:
        """Payment intents, page-paged. `from_`/`to` are ISO-8601 UTC instants."""
        params = _date_params(status, from_, to)
        return self.session.paginate(f"{_PA}/payment_intents",
                                     params=params or None, all_pages=all_pages, limit=limit)

    def get_payment_intent(self, intent_id) -> dict:
        """One payment intent by id."""
        data = self.session._get(f"{_PA}/payment_intents/{intent_id}")
        return data if isinstance(data, dict) else {}

    def create_payment_intent(self, body: dict) -> dict:
        """Create a payment intent (POST .../create); requests money from a shopper."""
        data = self.session._post(f"{_PA}/payment_intents/create", json=_with_request_id(body))
        return data if isinstance(data, dict) else {}

    def confirm_payment_intent(self, intent_id, body: dict) -> dict:
        """Confirm a payment intent (POST .../{id}/confirm); authorizes the payment."""
        data = self.session._post(f"{_PA}/payment_intents/{intent_id}/confirm",
                                  json=_with_request_id(body))
        return data if isinstance(data, dict) else {}

    def capture_payment_intent(self, intent_id, body: dict) -> dict:
        """Capture an authorized payment intent (POST .../{id}/capture); takes money."""
        data = self.session._post(f"{_PA}/payment_intents/{intent_id}/capture",
                                  json=_with_request_id(body))
        return data if isinstance(data, dict) else {}

    def cancel_payment_intent(self, intent_id, body: dict) -> dict:
        """Cancel a payment intent (POST .../{id}/cancel); voids the authorization."""
        data = self.session._post(f"{_PA}/payment_intents/{intent_id}/cancel",
                                  json=_with_request_id(body))
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # refunds (give money back against a payment intent)
    # ------------------------------------------------------------------

    def list_refunds(self, *, status=None, from_=None, to=None,
                     all_pages=False, limit=None) -> list:
        """Refunds, page-paged. `from_`/`to` are ISO-8601 UTC instants."""
        params = _date_params(status, from_, to)
        return self.session.paginate(f"{_PA}/refunds",
                                     params=params or None, all_pages=all_pages, limit=limit)

    def get_refund(self, refund_id) -> dict:
        """One refund by id."""
        data = self.session._get(f"{_PA}/refunds/{refund_id}")
        return data if isinstance(data, dict) else {}

    def create_refund(self, body: dict) -> dict:
        """Issue a refund (POST .../create); MOVES REAL MONEY back to the shopper."""
        data = self.session._post(f"{_PA}/refunds/create", json=_with_request_id(body))
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # customers (a saved shopper; no money)
    # ------------------------------------------------------------------

    def list_customers(self, *, from_=None, to=None, all_pages=False, limit=None) -> list:
        """Saved customers, page-paged. `from_`/`to` are ISO-8601 UTC instants."""
        params = _date_params(None, from_, to)
        return self.session.paginate(f"{_PA}/customers",
                                     params=params or None, all_pages=all_pages, limit=limit)

    def get_customer(self, customer_id) -> dict:
        """One customer by id."""
        data = self.session._get(f"{_PA}/customers/{customer_id}")
        return data if isinstance(data, dict) else {}

    def create_customer(self, body: dict) -> dict:
        """Create a customer from a full body (POST .../create)."""
        data = self.session._post(f"{_PA}/customers/create", json=body)
        return data if isinstance(data, dict) else {}

    def update_customer(self, customer_id, body: dict) -> dict:
        """Update a customer (POST .../update/{id} with the full body)."""
        data = self.session._post(f"{_PA}/customers/update/{customer_id}", json=body)
        return data if isinstance(data, dict) else {}

    def delete_customer(self, customer_id) -> dict:
        """Delete a customer (POST .../delete/{id})."""
        data = self.session._post(f"{_PA}/customers/delete/{customer_id}")
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # payment_consents (a saved mandate; read-only)
    # ------------------------------------------------------------------

    def list_payment_consents(self, *, status=None, from_=None, to=None,
                              all_pages=False, limit=None) -> list:
        """Payment consents (saved mandates), page-paged."""
        params = _date_params(status, from_, to)
        return self.session.paginate(f"{_PA}/payment_consents",
                                     params=params or None, all_pages=all_pages, limit=limit)

    def get_payment_consent(self, consent_id) -> dict:
        """One payment consent by id."""
        data = self.session._get(f"{_PA}/payment_consents/{consent_id}")
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # payment_links (a hosted page that collects a payment)
    # ------------------------------------------------------------------

    def list_payment_links(self, *, status=None, from_=None, to=None,
                           all_pages=False, limit=None) -> list:
        """Payment links, page-paged."""
        params = _date_params(status, from_, to)
        return self.session.paginate(f"{_PA}/payment_links",
                                     params=params or None, all_pages=all_pages, limit=limit)

    def get_payment_link(self, link_id) -> dict:
        """One payment link by id."""
        data = self.session._get(f"{_PA}/payment_links/{link_id}")
        return data if isinstance(data, dict) else {}

    def create_payment_link(self, body: dict) -> dict:
        """Create a payment link (POST .../create); publishes a public payment page."""
        data = self.session._post(f"{_PA}/payment_links/create", json=_with_request_id(body))
        return data if isinstance(data, dict) else {}
