"""A read-only export of a whole Rezdy account into a directory of JSON files.

Each file holds what the matching `--json` verb prints, walked to the end: the
lists page to their last page, a resource's sessions are cut by date, the extras
are found by prefix. Only list and get calls are made. A call that still fails
after the client's retries is written to the manifest instead of stopping the
export, so one bad record does not cost the rest, and `complete` in the manifest
says whether anything is missing.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

STAGES = ("products", "availability", "bookings", "customers", "extras", "pickup-lists",
          "categories", "rates", "resources", "vouchers", "company")
# The stages that read a window of sessions.
WINDOWED = ("availability", "resources")
# The stages that start from the product list.
NEEDS_PRODUCTS = ("availability", "company")


def _count(data) -> dict:
    """How much a file holds: its records, and its keys when it is a dict of them."""
    if isinstance(data, list):
        return {"records": len(data)}
    if isinstance(data, dict):
        return {"keys": len(data),
                "records": sum(len(v) if isinstance(v, list) else 1 for v in data.values())}
    return {"records": 1}


def _write_json(path: Path, data) -> None:
    """Write a file only its owner can read: customers and bookings are personal."""
    tmp = path.with_name(path.name + ".part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False, default=str)
        f.write("\n")
    os.replace(tmp, path)


def export_account(client, directory, *, start_local=None, end_local=None, stages=STAGES,
                   timezone_name=None, progress=lambda message: None) -> dict:
    """Write the chosen parts of the account into `directory`; return the manifest.

    `start_local` and `end_local` ('YYYY-MM-DD HH:mm:ss') bound the availability
    and the resource sessions. Bookings are all of them, whatever their dates.
    """
    out = Path(directory)
    if not out.exists():
        out.mkdir(parents=True, mode=0o700)
        os.chmod(out, 0o700)
    manifest = {
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timezone": timezone_name,
        "window": {"from": start_local, "to": end_local,
                   "applies_to": [s for s in STAGES if s in WINDOWED and s in stages]},
        "stages": [s for s in STAGES if s in stages],
        "files": {},
        "errors": [],
        "gaps": [],
    }

    def save(name, data):
        _write_json(out / name, data)
        manifest["files"][name] = _count(data)
        progress(f"{name}: {manifest['files'][name]['records']}")

    def attempt(what, call):
        """The call's result, or None with the failure written to the manifest."""
        try:
            return call()
        except Exception as e:
            manifest["errors"].append({"what": what, "error": str(e)})
            progress(f"FAILED {what}: {e}")
            return None

    def each(what, keys, call):
        """{key: call(key)} for the keys whose call succeeded."""
        got = {}
        for key in keys:
            value = attempt(f"{what} {key}", lambda key=key: call(key))
            if value is not None:
                got[str(key)] = value
        return got

    def wanted(stage):
        return stage in stages

    products = None
    if wanted("products") or any(wanted(s) for s in NEEDS_PRODUCTS):
        products = attempt("product list", client.all_products)
    codes = [p["productCode"] for p in products or [] if p.get("productCode")]
    for stage in NEEDS_PRODUCTS:
        if wanted(stage) and products is None:
            manifest["errors"].append({"what": stage, "error": "skipped: the product list failed"})

    details = None
    if wanted("products") and products is not None:
        save("products.json", products)
        details = each("product", codes, client.get_product)
        save("product-detail.json", details)
        save("product-pickups.json", each("product pickups", codes, client.get_product_pickups))

    if wanted("company") and products is not None:
        aliases = sorted({p["supplierAlias"] for p in products if p.get("supplierAlias")})
        save("company.json", each("company", aliases, client.get_company_by_alias))

    if wanted("availability") and products is not None:
        save("availability.json", each(
            "availability of", codes,
            lambda code: client.all_availability(code, start_local, end_local)))

    if wanted("bookings"):
        bookings = attempt("bookings", lambda: client.paginate(limit=100))
        if bookings is not None:
            save("bookings.json", bookings)

    if wanted("customers"):
        customers = attempt("customers", client.all_customers)
        if customers is not None:
            save("customers.json", customers)

    if wanted("extras"):
        walked = attempt("extras", client.all_extras)
        if walked is not None:
            extras, capped = walked
            manifest["gaps"] += [f"extras: more than 100 start with {c!r}" for c in capped]
            if details is not None:
                # The prefix walk cannot reach an extra whose name starts with
                # anything but a letter or digit; the products name the extras
                # they carry, and each name can be searched for whole.
                listed = {e.get("name") for e in extras}
                carried = {x.get("name") for d in details.values() for x in d.get("extras") or []}
                found = attempt("extras named by products",
                                lambda: client.extras_named(sorted(carried - listed - {None})))
                extras += found or []
                manifest["extras_found_by_name"] = len(found or [])
            else:
                manifest["extras_found_by_name"] = "not checked: products were not exported"
            save("extras.json", extras)

    if wanted("pickup-lists"):
        lists = attempt("pickup lists", client.list_pickup_lists)
        if lists is not None:
            save("pickup-lists.json", lists)
            ids = [x["id"] for x in lists if x.get("id") is not None]
            save("pickup-list-detail.json", each("pickup list", ids, client.get_pickup_list))

    if wanted("categories"):
        categories = attempt("categories", client.all_categories)
        if categories is not None:
            save("categories.json", categories)
            ids = [c["id"] for c in categories if c.get("id") is not None]
            save("category-products.json", each("category products of", ids,
                                                client.all_category_products))

    if wanted("rates"):
        rates = attempt("rates", client.list_rates)
        if rates is not None:
            save("rates.json", rates)
            ids = [r["rateId"] for r in rates if r.get("rateId") is not None]
            save("rate-detail.json", each("rate", ids, client.get_rate))

    if wanted("resources"):
        resources = attempt("resources", client.all_resources)
        if resources is not None:
            save("resources.json", resources)
            ids = [r["id"] for r in resources if r.get("id") is not None]
            sessions = {}
            for rid in ids:
                walked = attempt(f"sessions of resource {rid}",
                                 lambda rid=rid: client.all_resource_sessions(rid, start_local, end_local))
                if walked is not None:
                    sessions[str(rid)] = walked[0]
                    progress(f"resource {rid}: {len(walked[0])} sessions")
                    manifest["gaps"] += [f"resource {rid}: 100 or more sessions between {a} and {b}"
                                         for a, b in walked[1]]
            save("resource-sessions.json", sessions)

    if wanted("vouchers"):
        vouchers = attempt("vouchers", client.all_vouchers)
        if vouchers is not None:
            save("vouchers.json", vouchers)

    manifest["complete"] = not manifest["errors"] and not manifest["gaps"]
    _write_json(out / "manifest.json", manifest)
    return manifest
