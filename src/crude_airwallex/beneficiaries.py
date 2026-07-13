"""Airwallex Payouts — beneficiaries: the saved recipients a transfer pays to.

Full CRUD over ``/api/v1/beneficiaries``. Airwallex follows a POST-with-suffix
convention for the writes (``/create``, ``/update/{id}``, ``/delete/{id}``) while
the reads are GET on the collection and ``/{id}``. The list envelope is the usual
``{"items":[...], "has_more": bool}`` page (page_num/page_size). Create/update take
a full beneficiary body (``{"beneficiary": {...}, "nickname", "payer_entity_type",
"transfer_methods"}``) verbatim; the CLI passes the user's JSON through. Timestamp
fields are localized by the CLI layer, not here.
"""

from __future__ import annotations

from crude_common import asof


class BeneficiariesAPI:
    def __init__(self, session):
        self.session = session

    def list_beneficiaries(self, *, entity_type=None, from_=None, to=None,
                           all_pages=False, limit=None) -> list:
        """Saved beneficiaries, page-paged. `from_`/`to` are ISO-8601 UTC instants.

        Under WORLD_AS_OF ``to_created_at`` is clamped server-side and records
        are post-filtered on ``created_at``/``updated_at``.
        """
        asof.check_window_start(from_)
        params = {"entity_type": entity_type,
                  "from_created_at": from_,
                  "to_created_at": asof.clamp_upper_iso(to)}
        params = {k: v for k, v in params.items() if v is not None}
        items = self.session.paginate("/api/v1/beneficiaries",
                                      params=params or None, all_pages=all_pages, limit=limit)
        return asof.bound_records(items, "created_at", "updated_at", what="beneficiary")

    def get_beneficiary(self, beneficiary_id) -> dict:
        """One beneficiary by id."""
        data = self.session._get(f"/api/v1/beneficiaries/{beneficiary_id}")
        return data if isinstance(data, dict) else {}

    def create_beneficiary(self, body: dict) -> dict:
        """Create a beneficiary from a full body (POST /beneficiaries/create)."""
        data = self.session._post("/api/v1/beneficiaries/create", json=body)
        return data if isinstance(data, dict) else {}

    def update_beneficiary(self, beneficiary_id, body: dict) -> dict:
        """Update a beneficiary (POST /beneficiaries/update/{id} with the full body)."""
        data = self.session._post(f"/api/v1/beneficiaries/update/{beneficiary_id}", json=body)
        return data if isinstance(data, dict) else {}

    def delete_beneficiary(self, beneficiary_id) -> dict:
        """Delete a beneficiary (POST /beneficiaries/delete/{id})."""
        data = self.session._post(f"/api/v1/beneficiaries/delete/{beneficiary_id}")
        return data if isinstance(data, dict) else {}
