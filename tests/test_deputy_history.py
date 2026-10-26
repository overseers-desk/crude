"""`crude-deputy ... history <id>` prints a record's change log from Deputy.

Deputy keeps one row per field change for every live record, at
/resource/{Object}/{id}/HISTORY, while the record's own Modified field is only
the time of its last write. The stub here serves that log as Deputy does:
newest first, the Unix-second values as strings. The tests pin the request
path, the oldest-first order, the clock conversion in the table against the
raw --json, the WORLD_AS_OF drop of a change made after the cutoff, and the
note the client adds to Deputy's one error for an unknown or forbidden name.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from rich.console import Console
from typer.testing import CliRunner

from crude_common import asof
from crude_deputy import cli
from crude_deputy.client import UNAUTHORIZED_RESOURCE_NOTE, DeputyClient

runner = CliRunner()
BNE = ZoneInfo("Australia/Brisbane")


def _epoch(hh, mm):
    return str(int(datetime(2026, 10, 24, hh, mm, tzinfo=BNE).timestamp()))


# Deputy's order: newest first.
HISTORY = [
    {"Creator": "127", "CreatorName": "Jane Manager", "DtChanged": "2026-10-16T19:39:15+10:00",
     "Field": "Published", "NewValue": "1", "OldValue": "", "RecId": "12198"},
    {"Creator": "127", "CreatorName": "Jane Manager", "DtChanged": "2026-10-16T19:34:20+10:00",
     "Field": "StartTime", "NewValue": _epoch(8, 0), "OldValue": _epoch(7, 30), "RecId": "12198"},
]


@pytest.fixture
def requests_seen(monkeypatch):
    client = DeputyClient("t", "install", "au")
    seen = []

    def fake(method, url, **kw):
        seen.append((method, url.rsplit("/api/v1", 1)[-1]))
        return SimpleNamespace(status_code=200, ok=True, headers={}, content=b"x",
                               json=lambda: list(HISTORY))

    monkeypatch.setattr(client.session, "request", fake)
    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(cli, "read_config", lambda _p: {})
    monkeypatch.setattr(cli, "_make_client", lambda _c: client)
    monkeypatch.setattr(cli, "site_timezone", lambda _site: BNE)
    monkeypatch.setattr(cli, "console", Console(width=200))
    return seen


def test_json_is_raw_and_oldest_first(requests_seen):
    result = runner.invoke(cli.app, ["resource", "history", "Roster", "12198", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [r["Field"] for r in rows] == ["StartTime", "Published"]
    assert rows[0]["OldValue"] == _epoch(7, 30)
    assert requests_seen == [("GET", "/resource/Roster/12198/HISTORY")]


def test_table_shows_shift_times_as_clock(requests_seen):
    result = runner.invoke(cli.app, ["roster", "history", "12198"])
    assert result.exit_code == 0, result.output
    assert "2026-10-24 07:30" in result.output and "2026-10-24 08:00" in result.output
    assert _epoch(7, 30) not in result.output
    assert "2026-10-16 19:34:20" in result.output      # the stamp, to the second
    assert "Jane Manager" in result.output
    assert requests_seen == [("GET", "/resource/Roster/12198/HISTORY")]


@pytest.mark.parametrize("resource,obj", [
    ("employee", "Employee"), ("area", "OperationalUnit"),
    ("timesheet", "Timesheet"), ("leave", "Leave"),
])
def test_every_curated_resource_has_history(requests_seen, resource, obj):
    result = runner.invoke(cli.app, [resource, "history", "7", "--json"])
    assert result.exit_code == 0, result.output
    assert requests_seen == [("GET", f"/resource/{obj}/7/HISTORY")]


def test_world_as_of_drops_a_change_made_after_the_cutoff(requests_seen, monkeypatch):
    monkeypatch.setenv(asof.ENV, "2026-10-16T19:36:00+10:00")
    result = runner.invoke(cli.app, ["roster", "history", "12198", "--json"])
    assert result.exit_code == 0, result.output
    assert '"Field": "StartTime"' in result.output
    assert '"Field": "Published"' not in result.output


def _error(message):
    return SimpleNamespace(status_code=403, ok=False, headers={}, content=b"x",
                           json=lambda: {"error": {"code": 403, "message": message}})


def test_unknown_or_forbidden_name_is_said_to_be_either():
    client = DeputyClient("t", "install", "au")
    with pytest.raises(RuntimeError) as e:
        client._raise(_error("Unauthorized resource requested!"))
    assert UNAUTHORIZED_RESOURCE_NOTE in str(e.value)


def test_other_errors_pass_unchanged():
    client = DeputyClient("t", "install", "au")
    with pytest.raises(RuntimeError, match=r"Deputy API error: Object not found$"):
        client._raise(_error("Object not found"))
