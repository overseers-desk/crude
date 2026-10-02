"""`crude-deputy resource list` limits and pages through Deputy's QUERY endpoint.

Deputy's plain GET list ignores start and max: it answers with the same first
page whatever is asked. The stub here models that server, so a command that
still listed through GET would show the wrong rows, and its --all would never
see a short page.
"""

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from crude_deputy import cli
from crude_deputy.client import DeputyClient

runner = CliRunner()

# More than two full pages, so a walk has to stop on a short third one.
ROWS = [{"Id": n} for n in range(1, 1201)]


@pytest.fixture
def requests_seen(monkeypatch):
    """Serve ROWS as Deputy does; yield the (method, path) of each request."""
    client = DeputyClient("t", "install", "au")
    seen = []

    def fake(method, url, **kw):
        seen.append((method, url.rsplit("/api/v1", 1)[-1]))
        if method == "POST" and url.endswith("/QUERY"):
            body = kw.get("json") or {}
            start = body.get("start", 0)
            rows = ROWS[start:start + body.get("max", 500)]
        else:
            rows = ROWS[:500]
        return SimpleNamespace(status_code=200, ok=True, headers={}, content=b"x",
                               json=lambda: rows)

    monkeypatch.setattr(client.session, "request", fake)
    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(cli, "read_config", lambda _p: {})
    monkeypatch.setattr(cli, "_make_client", lambda _c: client)
    return seen


def _ids(result):
    assert result.exit_code == 0
    return [row["Id"] for row in json.loads(result.output)]


def test_limit_and_start_are_honoured(requests_seen):
    result = runner.invoke(
        cli.app, ["resource", "list", "Timesheet", "--limit", "3", "--start", "500", "--json"])
    assert _ids(result) == [501, 502, 503]
    assert requests_seen == [("POST", "/resource/Timesheet/QUERY")]


def test_all_walks_every_page_and_stops(requests_seen):
    result = runner.invoke(cli.app, ["resource", "list", "Timesheet", "--all", "--json"])
    assert _ids(result) == [row["Id"] for row in ROWS]
    assert len(requests_seen) == 3      # 500, 500, then the short page of 200
