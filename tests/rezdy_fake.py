"""An in-memory stand-in for the Rezdy Supplier API, for the rezdy tests.

It sits behind ``client.session.request`` and answers like the live API does where
that matters to a walk: the lists that page honour ``limit`` and ``offset``, the
availability search goes to 1000 a page, ``/extra`` answers a case-insensitive
prefix search with at most 100 extras and no paging, and
``/resources/{id}/sessions`` answers at most 100 sessions that start and end
inside a window and ignores ``offset``. Every record is made up.
"""

from types import SimpleNamespace

LOCAL = "%Y-%m-%d %H:%M:%S"


def ok(payload_key, payload):
    return {"requestStatus": {"success": True}, payload_key: payload}


class FakeRezdy:
    def __init__(self):
        self.calls = []            # (method, path, params) of every request
        self.products = []         # dicts with productCode, name, supplierAlias, extras
        self.customers = []
        self.vouchers = []
        self.categories = []
        self.category_products = {}  # category id -> list of products
        self.resources = []
        self.sessions_by_resource = {}   # resource id -> list of session dicts
        self.availability = {}     # product code -> list of session dicts
        self.bookings = []
        self.extras = []
        self.pickup_lists = []
        self.rates = []
        self.companies = {}        # alias -> company dict

    # -- the transport the client calls ---------------------------------

    def request(self, method, url, params=None, json=None):
        path = url.split("/v1", 1)[1]
        params = dict(params or {})
        self.calls.append((method, path, params))
        body = self.route(method, path, params)
        return SimpleNamespace(ok=True, status_code=200, json=lambda: body, headers={})

    def route(self, method, path, p):
        parts = [x for x in path.split("/") if x]
        head = parts[0]
        if head == "products":
            if len(parts) == 1:
                return ok("products", self.page(self.filtered(self.products, p, ("name", "productCode")), p))
            code = parts[1]
            product = next(x for x in self.products if x["productCode"] == code)
            if len(parts) == 3 and parts[2] == "pickups":
                return ok("pickups", product.get("pickups", []))
            return ok("product", product)
        if head == "customers":
            return ok("customers", self.page(self.filtered(self.customers, p, ("firstName", "lastName", "email")), p))
        if head == "vouchers":
            return ok("vouchers", self.page(self.filtered(self.vouchers, p, ("code",)), p))
        if head == "categories":
            if len(parts) == 1:
                rows = self.categories
                if p.get("visible") is not None:
                    rows = [c for c in rows if str(c.get("visible")).lower() == p["visible"]]
                return ok("categories", self.page(rows, p))
            if len(parts) == 3 and parts[2] == "products":
                return ok("products", self.page(self.category_products.get(parts[1], []), p))
            return ok("category", next(c for c in self.categories if str(c["id"]) == parts[1]))
        if head == "resources":
            if len(parts) == 1:
                return ok("resources", self.page(self.resources, p))
            return ok("sessions", self.resource_sessions(parts[1], p))
        if head == "availability":
            rows = [s for s in self.availability.get(p["productCode"], [])
                    if p["startTimeLocal"] <= s["startTimeLocal"] and s["endTimeLocal"] <= p["endTimeLocal"]]
            return ok("sessions", self.page(rows, p))
        if head == "bookings":
            return ok("bookings", self.page(self.bookings, p))
        if head == "extra":
            if len(parts) == 2:
                return ok("extra", next(e for e in self.extras if str(e["id"]) == parts[1]))
            return ok("extras", self.extra_search(p.get("searchString", "")))
        if head == "pickups":
            if len(parts) == 2:
                return ok("pickupList", next(x for x in self.pickup_lists if str(x["id"]) == parts[1]))
            return ok("pickupLists", list(self.pickup_lists))
        if head == "rates":
            if parts[1] == "search":
                return ok("rates", list(self.rates))
            return ok("rate", next(r for r in self.rates if str(r["rateId"]) == parts[1]))
        if head == "companies":
            return ok("company", self.companies[parts[2]])
        raise AssertionError(f"the fake does not serve {method} {path}")

    # -- how each kind of list answers ----------------------------------

    @staticmethod
    def page(rows, p):
        limit = int(p.get("limit", 20))
        offset = int(p.get("offset", 0))
        return rows[offset:offset + limit]

    @staticmethod
    def filtered(rows, p, fields):
        term = (p.get("search") or "").lower()
        if not term:
            return rows
        return [r for r in rows if any(term in str(r.get(f, "")).lower() for f in fields)]

    def extra_search(self, term):
        term = term.strip().lower()
        hits = [e for e in self.extras if e["name"].lower().startswith(term)]
        return sorted(hits, key=lambda e: -e["id"])[:100]

    def resource_sessions(self, resource_id, p):
        rows = [s for s in self.sessions_by_resource.get(resource_id, [])
                if p["startTimeLocal"] <= s["startTimeLocal"] and s["endTimeLocal"] <= p["endTimeLocal"]]
        return sorted(rows, key=lambda s: -s["id"])[:100]

    # -- what the tests ask afterwards ----------------------------------

    def methods(self):
        return {m for m, _path, _p in self.calls}

    def paths(self):
        return [path for _m, path, _p in self.calls]


def session(sid, start, end=None, product="PA"):
    """A made-up session; start and end are 'YYYY-MM-DD HH:mm:ss'."""
    return {"id": sid, "productCode": product, "startTimeLocal": start,
            "endTimeLocal": end or start, "seatsAvailable": 2}
