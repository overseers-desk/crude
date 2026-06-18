"""Render Clover orders into the Square item-level CSV shape.

The venue moved from Square to Clover; the existing sales analysis reads Square
item-level CSV exports. flatten lets one analysis span both eras by writing
Clover orders in Square's column shape: one row per line item, plus one
negative-``Net Sales`` row per refund (Square represents a refund as a negative
row, whereas Clover keeps refunds as separate objects on the order).

``Category`` carries the RAW Clover category, resolved from the catalog dump
(orders alone do not carry it). Mapping those raw categories into report
super-categories is the analysis's job, not crude's. ``Date``/``Time`` come from
``clientCreatedTime`` (POS-local), which is the field the analysis buckets by
hour-of-day and day-of-week — not ``createdTime`` (UTC).
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from zoneinfo import ZoneInfo

# The columns the Square analysis reads, by their exact export header names. The
# real export carries ~34 columns; the rest are unused by the analysis and are
# omitted rather than emitted empty.
COLUMNS = [
    "Date",
    "Time",
    "Time Zone",
    "Category",
    "Item",
    "Qty",
    "Net Sales",
    "Transaction ID",
    "Notes",
    "Event Type",
]


def build_category_map(catalog: dict) -> dict:
    """Map each inventory item id to its first category name (or "")."""
    out = {}
    for item in catalog.get("items", []):
        cats = (item.get("categories") or {}).get("elements") or []
        out[item.get("id")] = cats[0].get("name", "") if cats else ""
    return out


def _fmt_qty(qty) -> str:
    """A whole quantity as an int string, a fractional one to 3 dp."""
    return str(int(qty)) if float(qty) == int(qty) else f"{qty:.3f}"


def _line_qty(line_item: dict):
    """1 per line item, or unitQty (thousandths) for an item sold by measure."""
    unit_qty = line_item.get("unitQty")
    return unit_qty / 1000 if unit_qty else 1


def _line_net_cents(line_item: dict) -> int:
    """A line item's net in cents: price, plus modifications, less discounts.

    Clover modification ``amount`` is added to the price; discount ``amount`` is
    stored negative, so adding it subtracts. Mirrors Square's Net Sales (net of
    discounts and modifiers).
    """
    cents = line_item.get("price", 0) or 0
    for mod in (line_item.get("modifications") or {}).get("elements") or []:
        cents += mod.get("amount", 0) or 0
    for disc in (line_item.get("discounts") or {}).get("elements") or []:
        cents += disc.get("amount", 0) or 0
    return cents


def _local(ms, tz):
    return datetime.fromtimestamp(ms / 1000, tz)


def order_rows(order: dict, category_map: dict, tz, tz_name: str) -> list:
    """The Square-shape rows for one Clover order: line items, then refunds."""
    rows = []
    ms = order.get("clientCreatedTime") or order.get("createdTime")
    when = _local(ms, tz) if ms else None
    date_s = when.strftime("%Y-%m-%d") if when else ""
    time_s = when.strftime("%H:%M:%S") if when else ""
    txn = order.get("id", "")

    for line_item in (order.get("lineItems") or {}).get("elements") or []:
        item_id = (line_item.get("item") or {}).get("id")
        rows.append(
            {
                "Date": date_s,
                "Time": time_s,
                "Time Zone": tz_name,
                "Category": category_map.get(item_id, ""),
                "Item": line_item.get("name", ""),
                "Qty": _fmt_qty(_line_qty(line_item)),
                "Net Sales": f"{_line_net_cents(line_item) / 100:.2f}",
                "Transaction ID": txn,
                "Notes": "",
                "Event Type": "",
            }
        )

    for refund in (order.get("refunds") or {}).get("elements") or []:
        amount = refund.get("amount", 0) or 0
        r_ms = refund.get("clientCreatedTime") or refund.get("createdTime") or ms
        r_when = _local(r_ms, tz) if r_ms else when
        rows.append(
            {
                "Date": r_when.strftime("%Y-%m-%d") if r_when else date_s,
                "Time": r_when.strftime("%H:%M:%S") if r_when else time_s,
                "Time Zone": tz_name,
                "Category": "",
                "Item": "Refund",
                "Qty": "0",
                "Net Sales": f"{-abs(amount) / 100:.2f}",
                "Transaction ID": txn,
                "Notes": refund.get("reason") or "",
                "Event Type": "",
            }
        )
    return rows


def flatten(orders_path: str, catalog_path: str, out_path: str, tz_name: str) -> int:
    """Write a Square-shape CSV from an orders JSONL and a catalog dump.

    Returns the row count written. Streams the JSONL line by line so a year of
    orders (tens of MB) need not be held in memory.
    """
    tz = ZoneInfo(tz_name)
    with open(catalog_path) as f:
        category_map = build_category_map(json.load(f))

    written = 0
    with open(orders_path) as src, open(out_path, "w", newline="") as dst:
        writer = csv.DictWriter(dst, fieldnames=COLUMNS)
        writer.writeheader()
        for line in src:
            line = line.strip()
            if not line:
                continue
            for row in order_rows(json.loads(line), category_map, tz, tz_name):
                writer.writerow(row)
                written += 1
    return written
