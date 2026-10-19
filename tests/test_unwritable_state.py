"""A token or session that cannot be saved ends the command with one line.

These writes sat outside any ``try``, so an unwritable state directory (a
read-only sandbox, a full disk) ended the command in a traceback, and an AI
agent's transcript keeps whatever a traceback prints. Each now ends the command
with a line naming the file that could not be written. The tests run real
commands against a config on disk, with the state directory made read-only and
the login or token grant replaced by a fake, so nothing reaches a network.
"""

import json
import time

import pytest
from typer.testing import CliRunner

runner = CliRunner()

FAKE = "FAKE-CREDENTIAL-0123456789"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """Scratch config and state trees; yields (write_config, state_dir)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "config" / "crude").mkdir(parents=True)
    state = tmp_path / "state" / "crude"
    state.mkdir(parents=True)

    def write_config(text):
        (tmp_path / "config" / "crude" / "config.toml").write_text(text)

    yield write_config, state
    state.chmod(0o700)


def _ends_with_the_path(result, path):
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert lines[-1] == f"Error: could not write {path}"
    assert FAKE not in result.output


ATDW = '[atdw]\nusername = "u@example.com"\npassword = "pw"\n'
SKAL = '[skal]\nusername = "u@example.com"\npassword = "pw"\n'


@pytest.mark.parametrize("args", [["login"], ["listing", "list"]])
def test_atdw_token_write(tree, monkeypatch, args):
    import crude_atdw.auth
    from crude_atdw.cli import app

    write_config, state = tree
    write_config(ATDW)
    monkeypatch.setattr(crude_atdw.auth, "atdw_login", lambda u, p: FAKE)
    state.chmod(0o500)
    _ends_with_the_path(runner.invoke(app, args), state / "atdw_token")


@pytest.mark.parametrize("args", [["login"], ["member", "list"]])
def test_skal_session_write(tree, monkeypatch, args):
    import crude_skal.auth
    from crude_skal.cli import app

    write_config, state = tree
    write_config(SKAL)
    monkeypatch.setattr(crude_skal.auth, "skal_login", lambda u, p: FAKE)
    state.chmod(0o500)
    _ends_with_the_path(runner.invoke(app, args), state / "skal_session")


def test_skal_session_copied_from_config(tree):
    from crude_skal.cli import app

    write_config, state = tree
    write_config(f'[skal]\nsession_id = "{FAKE}"\n')
    state.chmod(0o500)
    _ends_with_the_path(runner.invoke(app, ["member", "list"]), state / "skal_session")


XERO = '[xero]\nclient_id = "fake-client"\n'


def test_xero_auth_token_write(tree, monkeypatch):
    import crude_xero.cli
    from crude_xero.cli import app

    write_config, state = tree
    write_config(XERO)
    grant = {"access_token": FAKE, "refresh_token": FAKE, "expires_in": 1800}
    monkeypatch.setattr(crude_xero.cli, "manual_authorize", lambda *a, **k: grant)
    state.chmod(0o500)
    _ends_with_the_path(runner.invoke(app, ["auth", "--manual"]), state / "xero_token.json")


def test_xero_refresh_token_write(tree, monkeypatch):
    import crude_xero.auth
    from crude_xero.cli import app

    write_config, state = tree
    write_config(XERO)
    expired = {"access_token": "old", "refresh_token": "old-refresh",
               "obtained_at": 0, "expires_at": time.time() - 3600, "scope": ""}
    (state / "xero_token.json").write_text(json.dumps(expired))
    (state / "xero_token.json.lock").touch()
    grant = {"access_token": FAKE, "refresh_token": FAKE, "expires_in": 1800}
    monkeypatch.setattr(crude_xero.auth, "refresh_token_grant", lambda *a, **k: grant)
    state.chmod(0o500)
    _ends_with_the_path(runner.invoke(app, ["tenants"]), state / "xero_token.json")


def test_xero_token_seeded_from_config(tree):
    from crude_xero.cli import app

    write_config, state = tree
    write_config(XERO + f'refresh_token = "{FAKE}"\n')
    state.chmod(0o500)
    _ends_with_the_path(runner.invoke(app, ["tenants"]), state / "xero_token.json")
