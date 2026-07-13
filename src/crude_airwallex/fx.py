"""Airwallex Payouts — FX: the current rate and balance conversions.

`get_current_rate` is the read-only price for a buy/sell currency pair (an indicative
rate, optionally for a given amount and value date). Conversions list/get read the
booked conversions; `create_conversion` books one and MOVES REAL MONEY, so the CLI
confirm-gates it. The conversion endpoints sit under ``/api/v1/fx/`` (list
``/fx/conversions``, get ``/fx/conversions/{id}``, create ``/fx/conversions/create``).
Like a transfer, a conversion body needs an idempotency ``request_id``; one is filled
when the caller omits it.

The FX rate and conversion endpoints are date-versioned and reject a request that
omits the version (``incorrect_version``, verified live); every FxAPI call sends the
``x-api-version`` header. The other product groups (core, beneficiaries, transfers)
answer on the account's default version and do not send it.
"""

from __future__ import annotations

import uuid

from crude_common import asof

# The Airwallex date-version the FX endpoints are pinned to. Verified live: the rate
# and conversions endpoints 400 with ``incorrect_version`` unless x-api-version is set.
FX_API_VERSION = "2024-06-30"
_FX_HEADERS = {"x-api-version": FX_API_VERSION}


class FxAPI:
    def __init__(self, session):
        self.session = session

    def get_current_rate(self, *, buy_currency, sell_currency,
                         buy_amount=None, sell_amount=None, conversion_date=None) -> dict:
        """The current indicative rate for a buy/sell pair (GET /fx/rates/current)."""
        params = {"buy_currency": buy_currency, "sell_currency": sell_currency,
                  "buy_amount": buy_amount, "sell_amount": sell_amount,
                  "conversion_date": conversion_date}
        params = {k: v for k, v in params.items() if v is not None}
        data = self.session._get("/api/v1/fx/rates/current", params=params, headers=_FX_HEADERS)
        return data if isinstance(data, dict) else {}

    def list_conversions(self, *, from_=None, to=None, all_pages=False, limit=None) -> list:
        """Booked FX conversions, page-paged. `from_`/`to` are ISO-8601 UTC instants.

        Under WORLD_AS_OF ``to_created_at`` is clamped server-side and records
        are post-filtered on ``created_at``/``updated_at``.
        """
        asof.check_window_start(from_)
        params = {"from_created_at": from_, "to_created_at": asof.clamp_upper_iso(to)}
        params = {k: v for k, v in params.items() if v is not None}
        items = self.session.paginate("/api/v1/fx/conversions",
                                      params=params or None, all_pages=all_pages, limit=limit,
                                      headers=_FX_HEADERS)
        return asof.bound_records(items, "created_at", "updated_at", what="conversion")

    def get_conversion(self, conversion_id) -> dict:
        """One conversion by id."""
        data = self.session._get(f"/api/v1/fx/conversions/{conversion_id}", headers=_FX_HEADERS)
        return data if isinstance(data, dict) else {}

    def create_conversion(self, body: dict) -> dict:
        """Book a conversion (POST /fx/conversions/create); MOVES REAL MONEY.

        A caller-supplied ``request_id`` wins; otherwise a fresh uuid4 is filled.
        """
        payload = {"request_id": str(uuid.uuid4()), **(body or {})}
        data = self.session._post("/api/v1/fx/conversions/create", json=payload, headers=_FX_HEADERS)
        return data if isinstance(data, dict) else {}
