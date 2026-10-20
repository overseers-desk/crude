"""crude_business_profile CLI tests (no network).

Pins the command surface (the resource groups on the root, auth and status as
root commands), that a write prompts unless --yes and then reaches the session,
that --location resolves through the session, and that the auth command's
token write lands in the side file in google-auth's shape.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from crude_business_profile import cli, cli_resources as r

runner = CliRunner()


def _cmds(typer_app):
    # A command registered with no explicit name carries none until Click builds it.
    return {c.name or c.callback.__name__ for c in typer_app.registered_commands}


def _groups(typer_app):
    return {g.name for g in typer_app.registered_groups}


def test_resource_command_surface():
    assert _cmds(r.location) == {"list"}
    assert _cmds(r.review) == {"list", "get", "reply", "unreply"}
    assert {"location", "review"} <= _groups(cli.app)
    assert {"auth", "status"} <= _cmds(cli.app)


class FakeSession:
    def __init__(self):
        self.info = {"account": "owner@example.com"}
        self.writes = []

    def resolve_location(self, spec, default=None):
        return f"resolved:{spec or default or 'only'}"

    def reviews(self, location, **kw):
        return [{"reviewId": "r1", "createTime": "2026-10-01T00:00:00Z",
                 "updateTime": "2026-10-01T00:00:00Z", "starRating": "FIVE",
                 "comment": "lovely", "reviewer": {"displayName": "Pat"}}]

    def reply(self, location, review_id, comment):
        self.writes.append(("reply", location, review_id, comment))
        return {"comment": comment}

    def delete_reply(self, location, review_id):
        self.writes.append(("unreply", location, review_id))
        return {}

    def accounts(self):
        return [{"name": "accounts/1", "accountName": "Riverside"}]


def _fake(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(cli, "_session", lambda: fake)
    monkeypatch.setattr(cli, "default_location", lambda: "the-default")
    return fake


def test_reply_prompts_without_yes_and_writes_with_it(monkeypatch):
    fake = _fake(monkeypatch)
    res = runner.invoke(cli.app, ["review", "reply", "r1", "-m", "Thank you"], input="n\n")
    assert res.exit_code != 0 and fake.writes == []
    res = runner.invoke(cli.app, ["review", "reply", "r1", "-m", "Thank you", "--yes", "-l", "cafe"])
    assert res.exit_code == 0, res.output
    assert fake.writes == [("reply", "resolved:cafe", "r1", "Thank you")]


def test_unreply_and_the_default_location(monkeypatch):
    fake = _fake(monkeypatch)
    res = runner.invoke(cli.app, ["review", "unreply", "r1", "--yes"])
    assert res.exit_code == 0, res.output
    assert fake.writes == [("unreply", "resolved:the-default", "r1")]


def test_review_list_renders_rows_and_json(monkeypatch):
    _fake(monkeypatch)
    res = runner.invoke(cli.app, ["review", "list"])
    assert res.exit_code == 0, res.output
    assert "Pat" in res.output and "1 review(s) found" in res.output
    res = runner.invoke(cli.app, ["review", "list", "--json"])
    assert json.loads(res.output)[0]["reviewId"] == "r1"


def test_status_names_the_google_account_and_the_accounts(monkeypatch):
    _fake(monkeypatch)
    res = runner.invoke(cli.app, ["status", "--json"])
    assert res.exit_code == 0, res.output
    rec = json.loads(res.output)
    assert rec["google_account"] == "owner@example.com"
    assert "Riverside (accounts/1)" == rec["accounts"]


def test_auth_writes_the_side_file_in_google_auths_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    config.write_text('[business-profile]\nclient_id = "cid"\nclient_secret = "cs"\n')
    monkeypatch.setattr("crude_common.config.find_config", lambda: config)
    monkeypatch.setattr(cli, "find_config", lambda: config)
    record = {"token": "t", "refresh_token": "rt", "token_uri": "u", "client_id": "cid",
              "client_secret": "cs", "scopes": ["s"], "expiry": "2026-01-01T00:00:00Z"}
    monkeypatch.setattr(cli, "manual_authorize", lambda *a, **k: dict(record))
    monkeypatch.setattr(cli, "_make_client", lambda info: FakeSession())
    res = runner.invoke(cli.app, ["auth", "--manual"])
    assert res.exit_code == 0, res.output
    path = tmp_path / "crude" / "business_profile_token.json"
    assert json.loads(path.read_text()) == record
    assert "Riverside" in res.output
