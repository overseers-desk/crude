"""What crude tells an agent about credentials leads it to no secret.

The Claude Code command is what an AI agent reads before running crude, and the
sample config is what a user copies from; a path or a hint in either that is
wrong sends the reader to look for, or type, a secret somewhere it should not be.
"""

import hashlib
import shutil
import subprocess
from importlib.resources import files

import pytest

from crude_common import claude_command


def test_the_command_gives_the_xero_token_path_the_code_uses(monkeypatch):
    from crude_xero.auth import token_store_path

    monkeypatch.setenv("XDG_STATE_HOME", "$XDG_STATE_HOME")
    assert str(token_store_path(None)) in claude_command.COMMAND


def test_the_sonas_hash_hint_reads_the_password_from_stdin():
    """The sample's password_hash hint hashes what it reads, so the password is
    never typed on a command line, where shell history keeps it."""
    if not (shutil.which("bash") and shutil.which("sha256sum")):
        pytest.skip("needs bash and sha256sum")
    sample = files("crude_common").joinpath("config.example.toml").read_text()
    line = next(ln for ln in sample.splitlines() if ln.startswith("password_hash"))
    hint = line.split("#", 1)[1].strip()
    out = subprocess.run(["bash", "-c", hint], input="fake pass\n",
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == hashlib.sha256(b"fake pass").hexdigest()
