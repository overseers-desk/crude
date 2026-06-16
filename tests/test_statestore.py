"""Unit tests for the shared durable state store — no network.

Pin the contract the site CLIs depend on: the path honours ``$XDG_STATE_HOME``
(falling back to ``~/.local/state``), the default account keeps the bare name
while a named account gets a ``_<account>`` suffix, and the write lands the file
atomically at mode 0600.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from crude_common.statestore import atomic_write, state_path


def test_state_path_honours_xdg_state_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert state_path("atdw_token") == tmp_path / "crude" / "atdw_token"


def test_state_path_falls_back_to_local_state(monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/someone")
    assert state_path("sonas_token") == Path("/home/someone/.local/state/crude/sonas_token")


def test_state_path_account_suffix(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert state_path("skal_session").name == "skal_session"
    assert state_path("skal_session", "es").name == "skal_session_es"


def test_atomic_write_is_0600_and_round_trips(tmp_path):
    path = tmp_path / "crude" / "tok"  # parent does not exist yet
    atomic_write(path, "secret-token")
    assert path.read_text() == "secret-token"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_atomic_write_overwrites_leaving_no_temp_files(tmp_path):
    path = tmp_path / "crude" / "tok"
    atomic_write(path, "first")
    atomic_write(path, "second")
    assert path.read_text() == "second"
    assert [p.name for p in path.parent.iterdir()] == ["tok"]
