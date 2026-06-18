"""crude_clover.flatten renders Clover orders into the Square CSV column shape.

These pin the pure transformation (no network): the category join from the
catalog, the clientCreatedTime split into local Date/Time, the net-of-discount
line amount, and the refund inverted into a negative-Net Sales row. Brisbane is
fixed so the local Date/Time is deterministic on any host.
"""

import csv
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from crude_clover.flatten import (
    COLUMNS,
    build_category_map,
    flatten,
    order_rows,
)

TZ_NAME = "Australia/Brisbane"
TZ = ZoneInfo(TZ_NAME)

# 2026-03-08 09:30:00 Brisbane (UTC+10), as the POS-local clientCreatedTime.
CLIENT_MS = int(datetime(2026, 3, 8, 9, 30, 0, tzinfo=TZ).timestamp() * 1000)

CATALOG = {
    "items": [
        {"id": "ITEM_COFFEE", "categories": {"elements": [{"id": "C1", "name": "Coffee"}]}},
        {"id": "ITEM_FEED", "categories": {"elements": [{"id": "C2", "name": "Animal Food"}]}},
        {"id": "ITEM_NOCAT", "categories": {"elements": []}},
    ],
    "categories": [],
    "modifierGroups": [],
}

ORDER = {
    "id": "ORD1",
    "clientCreatedTime": CLIENT_MS,
    "createdTime": CLIENT_MS,
    "lineItems": {
        "elements": [
            {
                "name": "Flat White",
                "item": {"id": "ITEM_COFFEE"},
                "price": 550,
                "modifications": {"elements": [{"amount": 50}]},  # +0.50 oat milk
                "discounts": {"elements": [{"amount": -100}]},  # -1.00 loyalty
            },
            {"name": "Hay Bag", "item": {"id": "ITEM_FEED"}, "price": 300},
        ]
    },
    "refunds": {"elements": [{"amount": 550, "reason": "wrong order"}]},
}


def test_build_category_map_first_category_or_empty():
    m = build_category_map(CATALOG)
    assert m["ITEM_COFFEE"] == "Coffee"
    assert m["ITEM_FEED"] == "Animal Food"
    assert m["ITEM_NOCAT"] == ""


def test_order_rows_lineitems_then_refund():
    rows = order_rows(ORDER, build_category_map(CATALOG), TZ, TZ_NAME)
    assert len(rows) == 3  # 2 line items + 1 refund

    coffee, feed, refund = rows

    # Category joined from the catalog; raw Clover name, not a super-category.
    assert coffee["Category"] == "Coffee"
    assert feed["Category"] == "Animal Food"

    # Local Date/Time from clientCreatedTime in Brisbane.
    assert coffee["Date"] == "2026-03-08"
    assert coffee["Time"] == "09:30:00"
    assert coffee["Time Zone"] == TZ_NAME

    # Net of the +0.50 modifier and -1.00 discount: 5.50 + 0.50 - 1.00 = 5.00.
    assert coffee["Net Sales"] == "5.00"
    assert feed["Net Sales"] == "3.00"
    assert coffee["Transaction ID"] == "ORD1"

    # Refund is a negative row carrying the reason, sharing the order id.
    assert refund["Item"] == "Refund"
    assert refund["Net Sales"] == "-5.50"
    assert refund["Notes"] == "wrong order"
    assert refund["Transaction ID"] == "ORD1"


def test_unitqty_weighed_item():
    order = {
        "id": "ORD2",
        "clientCreatedTime": CLIENT_MS,
        "lineItems": {"elements": [{"name": "Honey 500g", "item": {"id": "X"}, "price": 800, "unitQty": 500}]},
    }
    rows = order_rows(order, {}, TZ, TZ_NAME)
    assert rows[0]["Qty"] == "0.500"


def test_flatten_writes_square_header_and_rows(tmp_path):
    orders_path = tmp_path / "orders.jsonl"
    catalog_path = tmp_path / "catalog.json"
    out_path = tmp_path / "out.csv"
    orders_path.write_text(json.dumps(ORDER) + "\n")
    catalog_path.write_text(json.dumps(CATALOG))

    written = flatten(str(orders_path), str(catalog_path), str(out_path), TZ_NAME)
    assert written == 3

    with open(out_path, newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == COLUMNS
        out_rows = list(reader)
    assert [r["Item"] for r in out_rows] == ["Flat White", "Hay Bag", "Refund"]
    assert out_rows[2]["Net Sales"] == "-5.50"
