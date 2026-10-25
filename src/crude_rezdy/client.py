"""Rezdy Supplier API client: requests-based, API-key auth via the apiKey header.

The transport (`_request`) carries every verb; `_list`/`_one`/`_write` wrap it for
the three response shapes. Rezdy wraps each body as
``{"requestStatus": {...}, "<resource>": <payload>}``, so `_payload` pulls the
single non-status key by position rather than by a hardcoded name.

Paths and parameters here follow the published Supplier API OpenAPI document
(developers.rezdy.com/rezdyapi/index-supplier.html); the less-obvious ones
(singular ``/extra``, ``/rates/search``, availability keyed by product and
startTimeLocal, the manifest check-in pair) are taken from that spec, not assumed.
"""

from __future__ import annotations

import json
import time
from collections import deque
from urllib.parse import quote

import requests

from crude_common import asof

PROD_BASE = "https://api.rezdy.com"
STAGING_BASE = "https://api.rezdy-staging.com"

# Rezdy allows 100 requests a minute for the whole key, and counts every program
# using it. The client keeps to 80, so the other users of the key have room before
# a long walk tips the count over.
REQUESTS_PER_MINUTE = 80
# Retries after the first attempt, and the back-off between them: 2, 4, 8, 16, 32
# seconds unless Rezdy names a wait itself with Retry-After (never more than 120).
MAX_RETRIES = 5
BACKOFF_FIRST = 2.0
BACKOFF_CAP = 60.0
RETRY_AFTER_CAP = 120.0
# Rezdy's internal-call failure on a read: HTTP 500 with this name in the body.
DOWNSTREAM_FAILURE = "DOWNSTREAM_CALL_FAILURE"
# What Rezdy says when the minute's count is passed: in practice HTTP 406 (not 429)
# with errorCode "3" and a message beginning with this.
RATE_LIMIT_TEXT = "Too Many Requests"



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


def _seg(value: str) -> str:
    """URL-encode a path segment (startTimeLocal carries spaces and colons)."""
    return quote(str(value), safe="")


class RezdyClient:
    def __init__(self, api_key: str, environment: str = "production"):
        self.environment = environment
        self.base_url = STAGING_BASE if environment == "staging" else PROD_BASE
        self.session = requests.Session()
        # The key travels in the apiKey header, which keeps it out of request URLs
        # and out of any error that quotes one.
        self.session.headers.update(
            {"Accept": "application/json", "Content-Type": "application/json",
             "apiKey": api_key}
        )
        self._name_caches: dict = {}
        # Send times of the last minute's requests, and the clock and sleep that
        # pace them (replaced in tests so no test waits).
        self._sent: deque = deque()
        self._clock = time.monotonic
        self._sleep = time.sleep

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        """Wait, if the last minute already held REQUESTS_PER_MINUTE requests."""
        now = self._clock()
        while self._sent and now - self._sent[0] >= 60:
            self._sent.popleft()
        if len(self._sent) >= REQUESTS_PER_MINUTE:
            self._sleep(60 - (now - self._sent[0]))
            now = self._clock()
            # The oldest has had its minute, to the last rounding of the clock.
            self._sent.popleft()
            while self._sent and now - self._sent[0] >= 60:
                self._sent.popleft()
        self._sent.append(now)

    @staticmethod
    def _retry_wait(method: str, r, attempt: int):
        """Seconds to wait before trying again, or None when the answer is final.

        A refusal for the minute's count is retried for every verb: Rezdy refused
        the request unread. It arrives as HTTP 429 or, as Rezdy really sends it,
        HTTP 406 with "Too Many Requests" in the body. The 500 Rezdy gives when one
        of its own downstream calls fails is retried for reads only: on a write it
        does not say whether the change landed, and sending it twice could book
        twice.
        """
        backoff = min(BACKOFF_CAP, BACKOFF_FIRST * 2 ** attempt)

        def body_text():
            try:
                return json.dumps(r.json())
            except ValueError:
                return getattr(r, "text", "") or ""

        if r.status_code == 429 or (r.status_code == 406 and RATE_LIMIT_TEXT in body_text()):
            named = (getattr(r, "headers", None) or {}).get("Retry-After")
            try:
                return min(RETRY_AFTER_CAP, max(float(named), 1.0))
            except (TypeError, ValueError):
                return backoff
        if r.status_code == 500 and method == "GET" and DOWNSTREAM_FAILURE in body_text():
            return backoff
        return None

    def _request(self, method: str, path: str, params: dict = None, body: dict = None) -> dict:
        """Issue one request to /v1{path}, surfacing Rezdy's two error channels.

        Rezdy reports failure both via HTTP status and via a requestStatus object
        in the body (a success flag plus an error message). A write that returns no
        body (e.g. 204 on DELETE) yields an empty dict.

        Requests are paced to REQUESTS_PER_MINUTE, and a refusal for the minute's
        count, or a downstream failure on a read (see _retry_wait), is tried again with back-off up to
        MAX_RETRIES times before it is raised.
        """
        params = {k: v for k, v in (params or {}).items() if v is not None}
        retries = 0
        while True:
            self._throttle()
            r = self.session.request(
                method, f"{self.base_url}/v1{path}", params=params,
                json=body if body is not None else None,
            )
            wait = self._retry_wait(method, r, retries)
            if wait is None or retries >= MAX_RETRIES:
                break
            self._sleep(wait)
            retries += 1
        gave_up = f" (still failing after {retries} retries)" if retries else ""
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
            raise RuntimeError(f"Rezdy API error: {msg or f'HTTP {r.status_code}'}{gave_up}")
        return data

    def _list(self, path: str, params: dict = None) -> list:
        val = _payload(self._request("GET", path, params))
        return val if isinstance(val, list) else []

    def _one(self, path: str, params: dict = None) -> dict:
        val = _payload(self._request("GET", path, params))
        return val if isinstance(val, dict) else {}

    def _write(self, method: str, path: str, body: dict = None, params: dict = None):
        """One write (or now-valued POST) to the live API; refuses under WORLD_AS_OF.

        The quote verb also rides this path: a quote mutates nothing, but it is
        priced from the live present, which a bounded replay must not observe.
        """
        asof.guard_write(f"{method} {path}")
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

    def delete_product_image(self, product_code: str, media_id: str):
        return self._write("DELETE", f"/products/{product_code}/images/{media_id}")

    def get_product_pickups(self, product_code: str) -> list:
        return self._list(f"/products/{product_code}/pickups")

    # ------------------------------------------------------------------
    # Availability (sessions). Mutation keys a session by product + startTimeLocal.
    # ------------------------------------------------------------------

    def list_availability(
        self,
        product_code: str,
        start_time_local: str,
        end_time_local: str,
        min_availability: int = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        """Return sessions for a product within a local-time date range.

        Times are local, formatted 'YYYY-MM-DD HH:mm:ss'.
        """
        params = {
            "productCode": product_code,
            "startTimeLocal": start_time_local,
            "endTimeLocal": end_time_local,
            "limit": limit,
            "offset": offset,
        }
        if min_availability is not None:
            params["minAvailability"] = min_availability
        return self._list("/availability", params)

    def create_availability(self, body: dict) -> dict:
        return self._write("POST", "/availability", body)

    def update_availability(self, product_code: str, start_time_local: str, body: dict) -> dict:
        return self._write(
            "PUT",
            f"/availability/product/{product_code}/startTimeLocal/{_seg(start_time_local)}",
            body,
        )

    def delete_availability(self, product_code: str, start_time_local: str):
        return self._write(
            "DELETE",
            f"/availability/product/{product_code}/startTimeLocal/{_seg(start_time_local)}",
        )

    def batch_availability(self, body: dict) -> dict:
        return self._write("POST", "/availability/batch", body)

    # ------------------------------------------------------------------
    # Bookings
    # ------------------------------------------------------------------

    def list_bookings(
        self,
        order_status: str = None,
        search: str = None,
        product_code=None,
        min_tour_start: str = None,
        max_tour_start: str = None,
        min_date_created: str = None,
        max_date_created: str = None,
        updated_since: str = None,
        reseller_reference: str = None,
        source_channel: str = None,
        role: str = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list:
        """Search bookings.

        Time bounds are ISO 8601 instants, both ends inclusive except updatedSince,
        which Rezdy applies as "updated after". product_code takes one code or a
        list, sent as repeated productCode parameters. source_channel is the
        agent code.
        """
        params = {"limit": limit, "offset": offset}
        optional = {
            "orderStatus": order_status,
            "search": search,
            "productCode": product_code,
            "minTourStartTime": min_tour_start,
            "maxTourStartTime": max_tour_start,
            "minDateCreated": min_date_created,
            "maxDateCreated": max_date_created,
            "updatedSince": updated_since,
            "resellerReference": reseller_reference,
            "sourceChannel": source_channel,
            "role": role,
        }
        params.update({k: v for k, v in optional.items() if v})
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
    # Extras (note the singular path; the search term is required)
    # ------------------------------------------------------------------

    def list_extras(self, search: str = "") -> list:
        return self._list("/extra", {"searchString": search})

    def get_extra(self, extra_id: str) -> dict:
        return self._one(f"/extra/{extra_id}")

    def create_extra(self, body: dict) -> dict:
        return self._write("POST", "/extra", body)

    def update_extra(self, extra_id: str, body: dict) -> dict:
        return self._write("PUT", f"/extra/{extra_id}", body)

    def delete_extra(self, extra_id: str):
        return self._write("DELETE", f"/extra/{extra_id}")

    # ------------------------------------------------------------------
    # Pickup lists (path /pickups)
    # ------------------------------------------------------------------

    def list_pickup_lists(self, search: str = "") -> list:
        return self._list("/pickups", {"searchString": search})

    def get_pickup_list(self, pickup_list_id: str) -> dict:
        return self._one(f"/pickups/{pickup_list_id}")

    def create_pickup_list(self, body: dict) -> dict:
        return self._write("POST", "/pickups", body)

    def update_pickup_list(self, pickup_list_id: str, body: dict) -> dict:
        return self._write("PUT", f"/pickups/{pickup_list_id}", body)

    def delete_pickup_list(self, pickup_list_id: str):
        return self._write("DELETE", f"/pickups/{pickup_list_id}")

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    def list_categories(self, search: str = None, visible: bool = None,
                        limit: int = 100, offset: int = 0) -> list:
        """Categories, optionally by name and by visibility (None returns both)."""
        params = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        if visible is not None:
            params["visible"] = "true" if visible else "false"
        return self._list("/categories", params)

    def get_category(self, category_id: str) -> dict:
        return self._one(f"/categories/{category_id}")

    def list_category_products(self, category_id: str, limit: int = 100, offset: int = 0) -> list:
        return self._list(f"/categories/{category_id}/products", {"limit": limit, "offset": offset})

    def add_product_to_category(self, category_id: str, product_code: str):
        return self._write("PUT", f"/categories/{category_id}/products/{product_code}")

    def remove_product_from_category(self, category_id: str, product_code: str):
        return self._write("DELETE", f"/categories/{category_id}/products/{product_code}")

    # ------------------------------------------------------------------
    # Rates (list is /rates/search)
    # ------------------------------------------------------------------

    def list_rates(self, rate_name: str = None, product_code: str = None) -> list:
        return self._list("/rates/search", {"rateName": rate_name, "productCode": product_code})

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

    def list_resource_sessions(self, resource_id: str, start_time_local: str = None,
                               end_time_local: str = None, limit: int = 100,
                               offset: int = 0) -> list:
        """Sessions assigned to a resource, optionally within a local-time window."""
        params = {"limit": limit, "offset": offset}
        if start_time_local:
            params["startTimeLocal"] = start_time_local
        if end_time_local:
            params["endTimeLocal"] = end_time_local
        return self._list(f"/resources/{resource_id}/sessions", params)

    def list_session_resources(self, session_id: str = None, product_code: str = None,
                               start_time: str = None, start_time_local: str = None,
                               limit: int = 100, offset: int = 0) -> list:
        """Resources assigned to a session, found by session id or product + start time."""
        return self._list("/resources/session", {
            "sessionId": session_id, "productCode": product_code,
            "startTime": start_time, "startTimeLocal": start_time_local,
            "limit": limit, "offset": offset,
        })

    def add_session_to_resource(self, resource_id: str, session_id: str, resource_order: int = None):
        return self._write("PUT", f"/resources/{resource_id}/session/{session_id}",
                           params={"resourceOrder": resource_order})

    def remove_session_from_resource(self, resource_id: str, session_id: str):
        return self._write("DELETE", f"/resources/{resource_id}/session/{session_id}")

    # ------------------------------------------------------------------
    # Manifest (check-in). A session is keyed by productCode + start time;
    # the order variant also takes an orderNumber. checkin is the PUT toggle.
    # ------------------------------------------------------------------

    @staticmethod
    def _checkin_params(product_code, order_number=None, start_time=None,
                        start_time_local=None, checkin=None) -> dict:
        params = {"productCode": product_code, "startTime": start_time,
                  "startTimeLocal": start_time_local}
        if order_number is not None:
            params["orderNumber"] = order_number
        if checkin is not None:
            params["checkin"] = "true" if checkin else "false"
        return params

    def order_checkin_status(self, product_code, order_number=None, start_time=None, start_time_local=None) -> dict:
        return self._one("/manifest/checkinOrderSession",
                         self._checkin_params(product_code, order_number, start_time, start_time_local))

    def set_order_checkin(self, product_code, order_number=None, start_time=None, start_time_local=None, checkin=True):
        return self._write("PUT", "/manifest/checkinOrderSession",
                          params=self._checkin_params(product_code, order_number, start_time, start_time_local, checkin))

    def remove_order_checkin(self, order_number, product_code, start_time=None, start_time_local=None):
        return self._write("DELETE", "/manifest/checkinOrderSession",
                          params=self._checkin_params(product_code, order_number, start_time, start_time_local))

    def session_checkin_status(self, product_code, start_time=None, start_time_local=None) -> dict:
        return self._one("/manifest/checkinSession",
                         self._checkin_params(product_code, None, start_time, start_time_local))

    def set_session_checkin(self, product_code, start_time=None, start_time_local=None, checkin=True):
        return self._write("PUT", "/manifest/checkinSession",
                          params=self._checkin_params(product_code, None, start_time, start_time_local, checkin))

    def remove_session_checkin(self, product_code, start_time=None, start_time_local=None):
        return self._write("DELETE", "/manifest/checkinSession",
                          params=self._checkin_params(product_code, None, start_time, start_time_local))

    # ------------------------------------------------------------------
    # Vouchers (read-only, by code) and companies (read-only, by alias or name)
    # ------------------------------------------------------------------

    def list_vouchers(self, search: str = "", limit: int = 100, offset: int = 0) -> list:
        """Search vouchers; an empty search returns all."""
        return self._list("/vouchers", {"search": search, "limit": limit, "offset": offset})

    def get_voucher(self, voucher_code: str) -> dict:
        return self._one(f"/vouchers/{voucher_code}")

    def get_company_by_alias(self, company_alias: str) -> dict:
        return self._one(f"/companies/alias/{company_alias}")

    def get_company_by_name(self, company_name: str) -> dict:
        return self._one(f"/companies/name/{_seg(company_name)}")

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
