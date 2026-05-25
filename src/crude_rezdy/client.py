"""Rezdy Supplier API client — requests-based, API-key auth via query parameter."""

from __future__ import annotations

import requests

PROD_BASE = "https://api.rezdy.com"
STAGING_BASE = "https://api.rezdy-staging.com"


class RezdyClient:
    def __init__(self, api_key: str, environment: str = "production"):
        self.api_key = api_key
        self.environment = environment
        self.base_url = STAGING_BASE if environment == "staging" else PROD_BASE
        self.session = requests.Session()
        self.session.headers.update(
            {"Accept": "application/json", "Content-Type": "application/json"}
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict = None) -> dict:
        """GET /v1{path}, appending the API key and surfacing Rezdy errors.

        Rezdy reports failures both via HTTP status and via a requestStatus
        object in the body (success flag plus an error message).
        """
        params = dict(params or {})
        params["apiKey"] = self.api_key
        r = self.session.get(f"{self.base_url}/v1{path}", params=params)
        try:
            data = r.json()
        except ValueError:
            r.raise_for_status()
            raise
        status = data.get("requestStatus") if isinstance(data, dict) else None
        if not r.ok or (status and not status.get("success", True)):
            msg = ""
            if status:
                msg = (status.get("error") or {}).get("errorMessage", "")
            raise RuntimeError(f"Rezdy API error: {msg or f'HTTP {r.status_code}'}")
        return data

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    def list_products(self, search: str = None, limit: int = 20, offset: int = 0) -> list:
        """Search products by name, product code, or internal code."""
        params = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        return self._get("/products", params).get("products", [])

    def get_product(self, product_code: str) -> dict:
        """Return a single product by its product code (e.g. 'P12345')."""
        return self._get(f"/products/{product_code}").get("product", {})

    # ------------------------------------------------------------------
    # Availability (sessions)
    # ------------------------------------------------------------------

    def list_availability(
        self,
        product_code: str,
        start_time_local: str,
        end_time_local: str,
        min_availability: int = None,
        limit: int = 100,
    ) -> list:
        """Return sessions for a product within a local-time date range.

        Times are local, formatted 'YYYY-MM-DD HH:mm:ss'.
        """
        params = {
            "productCode": product_code,
            "startTimeLocal": start_time_local,
            "endTimeLocal": end_time_local,
            "limit": limit,
        }
        if min_availability is not None:
            params["minAvailability"] = min_availability
        return self._get("/availability", params).get("sessions", [])

    # ------------------------------------------------------------------
    # Bookings
    # ------------------------------------------------------------------

    def list_bookings(
        self,
        order_status: str = None,
        search: str = None,
        product_code: str = None,
        min_tour_start: str = None,
        max_tour_start: str = None,
        min_date_created: str = None,
        max_date_created: str = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list:
        """Search bookings. Tour-time and created-date bounds are ISO 8601."""
        params = {"limit": limit, "offset": offset}
        if order_status:
            params["orderStatus"] = order_status
        if search:
            params["search"] = search
        if product_code:
            params["productCode"] = product_code
        if min_tour_start:
            params["minTourStartTime"] = min_tour_start
        if max_tour_start:
            params["maxTourStartTime"] = max_tour_start
        if min_date_created:
            params["minDateCreated"] = min_date_created
        if max_date_created:
            params["maxDateCreated"] = max_date_created
        return self._get("/bookings", params).get("bookings", [])

    def get_booking(self, order_number: str) -> dict:
        """Return a single booking by order number (e.g. 'R123456')."""
        return self._get(f"/bookings/{order_number}").get("booking", {})
