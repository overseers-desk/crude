"""Per-event reads validate the event id instead of reporting an empty result.

Sonas publications accept any id, send `ready`, and publish nothing for an id
that matches nothing, so an unmatched event id used to read as "0 records" on
every per-event list. `_require_event` turns that into a "not found" error
before the read, the way `event get` already did.
"""

from types import SimpleNamespace

import pytest
import typer

from crude_sonas.cli import _require_event


def _client(docs):
    """A stub with the one method `_require_event` calls: read_pub -> docs."""
    return SimpleNamespace(read_pub=lambda name, params, **kwargs: docs)


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
