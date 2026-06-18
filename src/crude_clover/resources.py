"""Generic resource access and the resource registry for crude-clover.

Clover's API is uniform: a collection at ``/v3/merchants/{mId}/<segment>``, an
element at ``/<segment>/{id}``, ``expand`` for related objects, ``limit``/
``offset`` paging, POST to create (collection) or update (element), DELETE to
remove. ``ResourceAPI`` is the one home for that segment-to-path-and-verb logic,
shared by the registry-driven sub-apps and the generic ``resource`` passthrough.

``REGISTRY`` lists the curated resources, each a ``ResourceSpec`` (a named
structure, not behaviour): the CLI name, the path segment, table columns, a
default ``expand``, whether it is ``writable``, and whether it is a ``singleton``
(a get-only, id-less object such as merchant info). Columns are ``(header,
field-or-callable)`` as in crude-xero; callables format cents and epoch-ms times.
"""

from __future__ import annotations

from collections import namedtuple
from datetime import datetime
from itertools import islice

from crude_clover.client import PAGE

ResourceSpec = namedtuple("ResourceSpec", "name segment columns expand writable singleton")


def _spec(name, segment, columns, *, expand=None, writable=False, singleton=False):
    return ResourceSpec(name, segment, columns, expand, writable, singleton)


# --- column formatters -------------------------------------------------------

def cents(field):
    """A column rendering a cents field as dollars."""
    return lambda r: f"{(r.get(field) or 0) / 100:.2f}"


def ms_local(field):
    """A column rendering an epoch-ms field in the machine's local time."""
    def fmt(r):
        v = r.get(field)
        return datetime.fromtimestamp(v / 1000).strftime("%Y-%m-%d %H:%M") if v else ""
    return fmt


def _first(field, sub):
    """First element's ``sub`` field from an expanded ``{elements:[...]}`` child."""
    return lambda r: ((r.get(field) or {}).get("elements") or [{}])[0].get(sub, "")


def _customer_name(r):
    return " ".join(x for x in (r.get("firstName"), r.get("lastName")) if x)


def _role_name(r):
    roles = (r.get("roles") or {}).get("elements") or []
    return roles[0].get("name", "") if roles else (r.get("role") or "")


# --- the generic API ---------------------------------------------------------

class ResourceAPI:
    def __init__(self, session):
        self.session = session

    def _path(self, segment, rid=None):
        base = f"/v3/merchants/{self.session.merchant_id}"
        if segment:
            base = f"{base}/{segment}"
        return f"{base}/{rid}" if rid else base

    def list(self, segment, *, expand=None, filters=None, limit=None, all_pages=False):
        gen = self.session.iter_elements(self._path(segment), expand=expand, filters=filters)
        if all_pages:
            return list(gen)
        return list(islice(gen, limit or PAGE))

    def get(self, segment, rid=None, *, expand=None):
        params = [("expand", expand)] if expand else None
        return self.session.get(self._path(segment, rid), params=params)

    def create(self, segment, body):
        return self.session.post(self._path(segment), json=body)

    def update(self, segment, rid, body):
        return self.session.post(self._path(segment, rid), json=body)

    def delete(self, segment, rid):
        return self.session.delete(self._path(segment, rid))


# --- the registry ------------------------------------------------------------

REGISTRY = [
    # Inventory (writable)
    _spec("items", "items",
          [("ID", "id"), ("Name", "name"), ("Price", cents("price")), ("Code", "code")],
          expand="categories", writable=True),
    _spec("categories", "categories",
          [("ID", "id"), ("Name", "name"), ("Sort", "sortOrder")], writable=True),
    _spec("modifier-groups", "modifier_groups",
          [("ID", "id"), ("Name", "name")], expand="modifiers", writable=True),
    _spec("tags", "tags", [("ID", "id"), ("Name", "name")], writable=True),
    _spec("attributes", "attributes", [("ID", "id"), ("Name", "name")], writable=True),
    _spec("item-groups", "item_groups", [("ID", "id"), ("Name", "name")], writable=True),
    _spec("tax-rates", "tax_rates",
          [("ID", "id"), ("Name", "name"), ("Rate", "rate")], writable=True),
    _spec("item-stocks", "item_stocks",
          [("Item", lambda r: (r.get("item") or {}).get("id", "")), ("Qty", "quantity"),
           ("Stock", "stockCount")], writable=True),
    _spec("discounts", "discounts",
          [("ID", "id"), ("Name", "name"), ("Amount", cents("amount")), ("%", "percentage")],
          writable=True),
    # Customers and employees (writable)
    _spec("customers", "customers",
          [("ID", "id"), ("Name", _customer_name),
           ("Email", _first("emailAddresses", "emailAddress")),
           ("Phone", _first("phoneNumbers", "phoneNumber"))],
          expand="emailAddresses,phoneNumbers", writable=True),
    _spec("employees", "employees",
          [("ID", "id"), ("Name", "name"), ("Role", _role_name), ("Nickname", "nickname")],
          expand="roles", writable=True),
    # Merchant configuration (writable)
    _spec("order-types", "order_types",
          [("ID", "id"), ("Label", "label"), ("System", "systemOrderTypeId")], writable=True),
    _spec("tenders", "tenders",
          [("ID", "id"), ("Label", "label"), ("Enabled", "enabled")], writable=True),
    _spec("roles", "roles",
          [("ID", "id"), ("Name", "name"), ("System", "systemRole")], writable=True),
    _spec("tip-suggestions", "tip_suggestions",
          [("ID", "id"), ("Name", "name"), ("%", "percentage")], writable=True),
    _spec("opening-hours", "opening_hours", [("ID", "id"), ("Name", "name")], writable=True),
    # Merchant configuration (read-only)
    _spec("devices", "devices",
          [("ID", "id"), ("Name", "name"), ("Serial", "serial"), ("Model", "model")]),
    _spec("cash-events", "cash_events",
          [("ID", "id"), ("Type", "type"), ("Amount", cents("amountChange")),
           ("Time", ms_local("timestamp"))]),
    # Orders domain (read-only: created through payment flows, not plain POST)
    _spec("payments", "payments",
          [("ID", "id"), ("Amount", cents("amount")), ("Result", "result"),
           ("Time", ms_local("createdTime"))]),
    _spec("refunds", "refunds",
          [("ID", "id"), ("Amount", cents("amount")), ("Time", ms_local("createdTime"))]),
    _spec("credits", "credits",
          [("ID", "id"), ("Amount", cents("amount")), ("Time", ms_local("createdTime"))]),
    # Read-only singletons (get only, no id)
    _spec("merchant", "",
          [("ID", "id"), ("Name", "name"), ("Currency", "currency")], singleton=True),
    _spec("properties", "properties",
          [("Name", "merchantName"), ("Timezone", "timezone")], singleton=True),
    _spec("default-service-charge", "default_service_charge",
          [("ID", "id"), ("Name", "name"), ("%", "percentage")], singleton=True),
]

BY_NAME = {s.name: s for s in REGISTRY}
