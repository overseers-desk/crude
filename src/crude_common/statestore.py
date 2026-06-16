"""Durable per-account credential files under the XDG state directory.

A cached credential (an OAuth refresh token, a session cookie, a Meteor resume
token) is XDG *state*: it persists across restarts but is neither config (it is
program-managed, not user-authored) nor safe-to-delete cache (losing it costs a
re-login, which for some sites means a verification email or a browser consent).
Every crude site CLI keeps its cached credential here, in ``$XDG_STATE_HOME/crude``
(default ``~/.local/state/crude``), rather than in ``tempfile.gettempdir()`` where
a reboot or a ``tmpfiles`` sweep discards it. The path is independent of where
``config.toml`` was found, so a dev-tree config never lands a credential beside
the source.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def state_path(name: str, account: str | None = None) -> Path:
    """The durable state file ``name`` for ``account`` under ``$XDG_STATE_HOME``.

    Resolves to ``$XDG_STATE_HOME/crude/<name>`` (default base ``~/.local/state``).
    The default account keeps the bare ``name``; a named account gets a
    ``_<account>`` suffix so two accounts never read each other's file.
    """
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "crude" / (f"{name}_{account}" if account else name)


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically: temp file + fsync + os.replace, mode 0600.

    Creates the parent directory if needed. The 0600 mode matters because the
    file holds a bearer credential.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
