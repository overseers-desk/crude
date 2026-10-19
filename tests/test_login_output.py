"""A login prints no part of the credential it obtained.

crude-atdw login printed the JWT's first 40 characters and crude-skal login the
session cookie's first 16, into the transcript of the AI agent that ran it. The
login itself is a fake here, so nothing reaches a network.
"""

import pytest
from typer.testing import CliRunner

runner = CliRunner()

FAKE = "FAKE-CREDENTIAL-0123456789abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "config" / "crude").mkdir(parents=True)
    path = tmp_path / "config" / "crude" / "config.toml"
    return path.write_text


def test_atdw_login_prints_no_part_of_the_token(config_dir, monkeypatch):
    import crude_atdw.auth
    from crude_atdw.cli import app

    config_dir('[atdw]\nusername = "u@example.com"\npassword = "pw"\n')
    monkeypatch.setattr(crude_atdw.auth, "atdw_login", lambda u, p: FAKE)
    result = runner.invoke(app, ["login"])
    assert result.exit_code == 0
    assert "Login successful" in result.output
    assert FAKE[:8] not in result.output


def test_skal_login_prints_no_part_of_the_session(config_dir, monkeypatch):
    import crude_skal.auth
    from crude_skal.cli import app

    config_dir('[skal]\nusername = "u@example.com"\npassword = "pw"\n')
    monkeypatch.setattr(crude_skal.auth, "skal_login", lambda u, p: FAKE)
    result = runner.invoke(app, ["login"])
    assert result.exit_code == 0
    assert "Login successful" in result.output
    assert FAKE[:8] not in result.output
