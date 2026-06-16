"""Rezdy Supplier API client — requests-based, API-key auth via query parameter.

The transport (`_request`) carries every verb; `_list`/`_one`/`_write` wrap it for
the three response shapes. Rezdy wraps each body as
``{"requestStatus": {...}, "<resource>": <payload>}``, so `_payload` pulls the
single non-status key by position rather than by a hardcoded name — one fewer
thing to guess wrong for the less-travelled resources.
"""

from __future__ import annotations

import requests

PROD_BASE = "https://api.rezdy.com"
STAGING_BASE = "https://api.rezdy-staging.com"


def _payload(data):
    """Return a response's resource value: the one key besides requestStatus.

    Falls back to the whole dict when the shape is not the usual single-payload
    one (zero or several keys), so nothing is silently dropped.
    """
    if not isinstance(data, dict):
        return data
    rest = [k for k in data if k != "requestStatus"]
    if len(rest) == 1:
        return data[rest[0]]
    return data


class RezdyClient:
    def __init__(self, api_key: str, environment: str = "production"):
        self.api_key = api_key
        self.environment = environment
        self.base_url = STAGING_BASE if environment == "staging" else PROD_BASE
        self.session = requests.Session()
        self.session.headers.update(
            {"Accept": "application/json", "Content-Type": "application/json"}
        )
        self._name_caches: dict = {}

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, params: dict = None, body: dict = None) -> dict:
        """Issue one request to /v1{path}, surfacing Rezdy's two error channels.

        Rezdy reports failure both via HTTP status and via a requestStatus object
        in the body (a success flag plus an error message). A write that returns no
        body (e.g. 204 on DELETE) yields an empty dict.
        """
        params = dict(params or {})
        params["apiKey"] = self.api_key
        r = self.session.request(
            method, f"{self.base_url}/v1{path}", params=params,
            json=body if body is not None else None,
        )
        try:
            data = r.json()
        except ValueError:
            if r.ok:
                return {}
            r.raise_for_status()
            raise
        status = data.get("requestStatus") if isinstance(data, dict) else None
        if not r.ok or (status and not status.get("success", True)):
            msg = ""
            if status:
                msg = (status.get("error") or {}).get("errorMessage", "")
            raise RuntimeError(f"Rezdy API error: {msg or f'HTTP {r.status_code}'}")
        return data

    def _list(self, path: str, params: dict = None) -> list:
        val = _payload(self._request("GET", path, params))
        return val if isinstance(val, list) else []

    def _one(self, path: str, params: dict = None) -> dict:
        val = _payload(self._request("GET", path, params))
        return val if isinstance(val, dict) else {}

    def _write(self, method: str, path: str, body: dict = None, params: dict = None):
        return _payload(self._request(method, path, params, body))

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    def list_products(self, search: str = None, limit: int = 20, offset: int = 0) -> list:
        """Search products by name, product code, or internal code."""
        params = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        return self._list("/products", params)

    def get_product(self, product_code: str) -> dict:
        """Return a single product by its product code (e.g. 'P12345')."""
        return self._one(f"/products/{product_code}")

    def create_product(self, body: dict) -> dict:
        return self._write("POST", "/products", body)

    def update_product(self, product_code: str, body: dict) -> dict:
        return self._write("PUT", f"/products/{product_code}", body)

    def delete_product(self, product_code: str):
        return self._write("DELETE", f"/products/{product_code}")

    def add_product_image(self, product_code: str, body: dict) -> dict:
        return self._write("POST", f"/products/{product_code}/images", body)

    def delete_product_image(self, product_code: str, image_id: str):
        return self._write("DELETE", f"/products/{product_code}/images/{image_id}")

    def get_product_pickups(self, product_code: str) -> list:
        return self._list(f"/products/{product_code}/pickups")

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
        return self._list("/availability", params)

    def create_availability(self, body: dict) -> dict:
        return self._write("POST", "/availability", body)

    def update_availability(self, session_id: str, body: dict) -> dict:
        return self._write("PUT", f"/availability/{session_id}", body)

    def delete_availability(self, session_id: str):
        return self._write("DELETE", f"/availability/{session_id}")

    def batch_availability(self, body: dict) -> dict:
        return self._write("POST", "/availability/batch", body)

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
        return self._list("/bookings", params)

    def get_booking(self, order_number: str) -> dict:
        """Return a single booking by order number (e.g. 'R123456')."""
        return self._one(f"/bookings/{order_number}")

    def create_booking(self, body: dict) -> dict:
        return self._write("POST", "/bookings", body)

    def update_booking(self, order_number: str, body: dict) -> dict:
        return self._write("PUT", f"/bookings/{order_number}", body)

    def cancel_booking(self, order_number: str):
        return self._write("DELETE", f"/bookings/{order_number}")

    def quote_booking(self, body: dict) -> dict:
        return self._write("POST", "/bookings/quote", body)

    def paginate(self, limit: int = 100, **kwargs) -> list:
        """Fetch all pages from list_bookings, incrementing offset until a short page."""
        results, offset = [], 0
        while True:
            page = self.list_bookings(limit=limit, offset=offset, **kwargs)
            results.extend(page)
            if len(page) < limit:
                break
            offset += limit
        return results

    # ------------------------------------------------------------------
    # Customers
    # ------------------------------------------------------------------

    def list_customers(self, search: str = None, limit: int = 20, offset: int = 0) -> list:
        params = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        return self._list("/customers", params)

    def get_customer(self, customer_id: str) -> dict:
        return self._one(f"/customers/{customer_id}")

    def create_customer(self, body: dict) -> dict:
        return self._write("POST", "/customers", body)

    def delete_customer(self, customer_id: str):
        return self._write("DELETE", f"/customers/{customer_id}")

    # ------------------------------------------------------------------
    # Extras
    # ------------------------------------------------------------------

    def list_extras(self, limit: int = 100, offset: int = 0) -> list:
        return self._list("/extras", {"limit": limit, "offset": offset})

    def get_extra(self, extra_id: str) -> dict:
        return self._one(f"/extras/{extra_id}")

    def create_extra(self, body: dict) -> dict:
        return self._write("POST", "/extras", body)

    def update_extra(self, extra_id: str, body: dict) -> dict:
        return self._write("PUT", f"/extras/{extra_id}", body)

    def delete_extra(self, extra_id: str):
        return self._write("DELETE", f"/extras/{extra_id}")

    # ------------------------------------------------------------------
    # Pickup lists
    # ------------------------------------------------------------------

    def list_pickup_lists(self, limit: int = 100, offset: int = 0) -> list:
        return self._list("/pickup-lists", {"limit": limit, "offset": offset})

    def get_pickup_list(self, pickup_list_id: str) -> dict:
        return self._one(f"/pickup-lists/{pickup_list_id}")

    def create_pickup_list(self, body: dict) -> dict:
        return self._write("POST", "/pickup-lists", body)

    def update_pickup_list(self, pickup_list_id: str, body: dict) -> dict:
        return self._write("PUT", f"/pickup-lists/{pickup_list_id}", body)

    def delete_pickup_list(self, pickup_list_id: str):
        return self._write("DELETE", f"/pickup-lists/{pickup_list_id}")

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    def list_categories(self, limit: int = 100, offset: int = 0) -> list:
        return self._list("/categories", {"limit": limit, "offset": offset})

    def get_category(self, category_id: str) -> dict:
        return self._one(f"/categories/{category_id}")

    def list_category_products(self, category_id: str, limit: int = 100, offset: int = 0) -> list:
        return self._list(f"/categories/{category_id}/products", {"limit": limit, "offset": offset})

    def add_product_to_category(self, category_id: str, product_code: str):
        return self._write("PUT", f"/categories/{category_id}/products/{product_code}")

    def remove_product_from_category(self, category_id: str, product_code: str):
        return self._write("DELETE", f"/categories/{category_id}/products/{product_code}")

    # ------------------------------------------------------------------
    # Rates
    # ------------------------------------------------------------------

    def list_rates(self, limit: int = 100, offset: int = 0) -> list:
        return self._list("/rates", {"limit": limit, "offset": offset})

    def get_rate(self, rate_id: str) -> dict:
        return self._one(f"/rates/{rate_id}")

    def add_product_to_rate(self, rate_id: str, product_code: str):
        return self._write("PUT", f"/rates/{rate_id}/products/{product_code}")

    def remove_product_from_rate(self, rate_id: str, product_code: str):
        return self._write("DELETE", f"/rates/{rate_id}/products/{product_code}")

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------

    def list_resources(self, limit: int = 100, offset: int = 0) -> list:
        return self._list("/resources", {"limit": limit, "offset": offset})

    def list_resource_sessions(self, resource_id: str) -> list:
        return self._list(f"/resources/{resource_id}/sessions")

    def list_session_resources(self, session_id: str) -> list:
        return self._list(f"/resources/sessions/{session_id}")

    def add_session_to_resource(self, resource_id: str, session_id: str):
        return self._write("PUT", f"/resources/{resource_id}/sessions/{session_id}")

    def remove_session_from_resource(self, resource_id: str, session_id: str):
        return self._write("DELETE", f"/resources/{resource_id}/sessions/{session_id}")

    # ------------------------------------------------------------------
    # Manifest (check-in)
    # ------------------------------------------------------------------

    def get_order_checkin(self, order_number: str) -> dict:
        return self._one(f"/manifest/checkin/{order_number}")

    def checkin_order(self, order_number: str, body: dict = None) -> dict:
        return self._write("PUT", f"/manifest/checkin/{order_number}", body)

    def uncheck_order(self, order_number: str):
        return self._write("DELETE", f"/manifest/checkin/{order_number}")

    def get_session_checkin(self, session_id: str) -> dict:
        return self._one(f"/manifest/checkin/session/{session_id}")

    def checkin_session(self, session_id: str, body: dict = None) -> dict:
        return self._write("PUT", f"/manifest/checkin/session/{session_id}", body)

    def uncheck_session(self, session_id: str):
        return self._write("DELETE", f"/manifest/checkin/session/{session_id}")

    # ------------------------------------------------------------------
    # Vouchers (read-only) and companies (read-only)
    # ------------------------------------------------------------------

    def list_vouchers(self, limit: int = 100, offset: int = 0) -> list:
        return self._list("/vouchers", {"limit": limit, "offset": offset})

    def get_voucher(self, voucher_id: str) -> dict:
        return self._one(f"/vouchers/{voucher_id}")

    def get_company(self, company_alias: str = None) -> dict:
        """Get the caller's company, or a named one by alias."""
        if company_alias:
            return self._one(f"/companies/{company_alias}")
        return self._one("/companies")

    # ------------------------------------------------------------------
    # Name resolution (id/code -> human name), cached per kind
    # ------------------------------------------------------------------

    def product_names(self) -> dict:
        """Cached {productCode: name}, for annotating bare codes in output."""
        if "products" not in self._name_caches:
            mapping = {}
            offset = 0
            while True:
                page = self.list_products(limit=100, offset=offset)
                for p in page:
                    code = p.get("productCode")
                    if code:
                        mapping[code] = p.get("name", "")
                if len(page) < 100:
                    break
                offset += 100
            self._name_caches["products"] = mapping
        return self._name_caches["products"]
