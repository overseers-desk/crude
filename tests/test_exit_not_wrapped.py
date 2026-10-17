"""A helper's own exit is not reported a second time by the command around it.

A helper that prints its message and raises ``typer.Exit(1)`` (a config value
missing, a timezone unknown) raises a ``RuntimeError``, so an unguarded
``except Exception`` around the work catches it and prints a junk
``Error fetching x: 1`` or ``Error: create x: 1`` (the ``str`` of the exit)
under the real message. These run real commands through the
app against a config on disk that triggers the helper's exit; nothing reaches a
network, because every one stops before any request.
"""

import pytest
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Point config and token state at a scratch tree; return a config writer."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "config" / "crude").mkdir(parents=True)

    def write(text):
        (tmp_path / "config" / "crude" / "config.toml").write_text(text)

    return write


def _lines(result):
    return [ln for ln in result.output.splitlines() if ln.strip()]


def test_xero_read_with_unconfigured_account_prints_only_the_real_error(config_dir):
    from crude_xero.cli import app

    config_dir("[xero]\n")
    result = runner.invoke(app, ["invoice", "list"])
    assert result.exit_code == 1
    assert _lines(result) == ["Error: [xero] must set client_id."]


def test_xero_get_with_unconfigured_account_prints_only_the_real_error(config_dir):
    from crude_xero.cli import app

    config_dir("[xero]\n")
    result = runner.invoke(app, ["invoice", "get", "ID-1"])
    assert result.exit_code == 1
    assert _lines(result) == ["Error: [xero] must set client_id."]


def test_write_with_unconfigured_account_prints_only_the_real_error(config_dir):
    from crude_xero.cli import app

    config_dir("[xero]\n")
    result = runner.invoke(app, ["invoice", "create", "--data", "{}", "--yes"])
    assert result.exit_code == 1
    assert _lines(result) == ["Error: [xero] must set client_id."]


def test_sonas_read_with_unknown_timezone_prints_only_the_real_error(config_dir):
    from crude_sonas.cli import app

    config_dir('[sonas]\nusername = "u"\npassword_hash = "h"\ntimezone = "Not/AZone"\n')
    result = runner.invoke(app, ["availability", "list", "--from", "2026-01-01"])
    assert result.exit_code == 1
    assert _lines(result) == [
        "Error: unknown timezone 'Not/AZone' in config.toml; use an IANA name "
        "like Australia/Brisbane."
    ]
