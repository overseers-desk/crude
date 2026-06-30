"""Per-event reads validate the event id instead of reporting an empty result.

Sonas publications accept any id, send `ready`, and publish nothing for an id
that matches nothing, so an unmatched event id used to read as "0 records" on
every per-event list. `_require_event` turns that into a "not found" error
before the read, the way `event get` already did.
"""

from types import SimpleNamespace

import pytest
import typer

from crude_sonas.cli import _bundle_event, _index_row, _require_event


def _client(docs):
    """A stub with the one method `_require_event` calls: read_pub -> docs."""
    return SimpleNamespace(read_pub=lambda name, params, **kwargs: docs)


def _multi_client(by_pub):
    """Stub whose read_pub dispatches by publication name (for export tests)."""
    return SimpleNamespace(read_pub=lambda name, params, **kwargs: by_pub.get(name, []))


def test_require_event_missing_raises():
    # No events doc came back for the id: it does not exist.
    with pytest.raises(typer.Exit) as exc:
        _require_event(_client([]), "NOTAREALID")
    assert exc.value.exit_code == 1


def test_require_event_present_passes():
    # The eventBasicInfo events doc is present: the id is valid, no raise.
    docs = [{"_collection": "events", "_id": "KsXbzkkAfYnqcrJyZ"}]
    _require_event(_client(docs), "KsXbzkkAfYnqcrJyZ")


def test_require_event_other_collections_only_raises():
    # Sibling collections without the events doc still count as not found.
    docs = [{"_collection": "timelines", "eventId": "x"}]
    with pytest.raises(typer.Exit):
        _require_event(_client(docs), "x")


def test_bundle_event_partitions_multi_cursor_pubs():
    # eventBasicInfo and eventServiceBookings each carry several collections;
    # the bundle splits them into the right buckets.
    by_pub = {
        "eventBasicInfo": [
            {"_collection": "events", "_id": "E1", "status": 0},
            {"_collection": "venues", "_id": "V1", "name": "Hall"},
            {"_collection": "timelines", "_id": "T1"},
        ],
        "eventServiceBookings": [
            {"_collection": "service-bookings", "_id": "B1"},
            {"_collection": "services", "_id": "S1", "name": "Catering"},
        ],
        "eventCustomersInfo": [{"_id": "U1", "email": "a@example.com"}],
        "eventMessages": [{"_id": "M1"}],
    }
    bundle = _bundle_event(_multi_client(by_pub), "E1")
    assert bundle["event"]["_id"] == "E1"
    assert bundle["venue"]["_id"] == "V1"
    assert [d["_id"] for d in bundle["service_bookings"]] == ["B1"]
    assert [d["_id"] for d in bundle["services"]] == ["S1"]
    assert bundle["messages"] == [{"_id": "M1"}]


def test_index_row_pulls_contact_from_customers_info():
    # The event's customers stub has no email; it comes from the users doc that
    # eventCustomersInfo publishes, joined on the main customer's userId.
    bundle = {
        "event_id": "E1",
        "event": {"status": 0, "type": 0,
                  "customers": [{"main": True, "userId": "U1",
                                 "firstname": "A", "lastname": "B"}]},
        "customers_info": [{"_id": "U1", "email": "a@example.com"}],
        "messages": [1, 2],
        "transactions": [],
        "financial_records": [3],
    }
    row = _index_row(bundle)
    assert row["status"] == "Enquiry"
    assert row["type"] == "Wedding"
    assert row["name"] == "A B"
    assert row["email"] == "a@example.com"
    assert row["date"] == ""
    assert row["messages"] == 2
    assert row["financial_records"] == 1
