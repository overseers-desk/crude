"""`template edit` composes the right templateUpdate modifier, offline.

The verb is the venue-template write path (a type-8 template is the T&C/policy
body new couples sign). These tests intercept the DDP call so they assert the
{templateId, modifier} payload without touching the network.
"""

import crude_sonas.cli as cli
from typer.testing import CliRunner

runner = CliRunner()


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_do_call", lambda *a, **k: calls.append((a, k)))
    return calls


def test_edit_shortcuts_build_set(monkeypatch):
    calls = _capture(monkeypatch)
    r = runner.invoke(cli.app, ["template", "edit", "TID",
                                "--subject", "New subject", "--name", "v2", "--yes"])
    assert r.exit_code == 0
    (method, arg, _what), _ = calls[0]
    assert method == "templateUpdate"
    assert arg == {"templateId": "TID",
                   "modifier": {"$set": {"subject": "New subject", "name": "v2"}}}


def test_edit_raw_data_passes_through(monkeypatch):
    calls = _capture(monkeypatch)
    r = runner.invoke(cli.app, ["template", "edit", "TID",
                                "--data", '{"$set": {"body": "<p>x</p>"}}', "--yes"])
    assert r.exit_code == 0
    (_method, arg, _what), _ = calls[0]
    assert arg["modifier"] == {"$set": {"body": "<p>x</p>"}}


def test_edit_without_modifier_errors(monkeypatch):
    calls = _capture(monkeypatch)
    r = runner.invoke(cli.app, ["template", "edit", "TID", "--yes"])
    assert r.exit_code == 2
    assert not calls
