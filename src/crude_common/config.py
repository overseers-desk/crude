"""Config discovery and reading shared across the crude site CLIs.

One config file (``~/.config/crude/config.toml``) holds a section per site, so
locating and parsing it is the same job for every CLI; it lives here rather than
being copied into each.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import typer

# The account selected for this invocation, set by the shared root callback from
# --account/-a (or $CRUDE_ACCOUNT). None means the default account: the bare
# [site] section's own keys. Process-global because each crude binary runs as one
# process and the callback fires before any command reads config.
_account: Optional[str] = None


def set_account(name: Optional[str]) -> None:
    global _account
    _account = name


def account() -> Optional[str]:
    return _account


def find_config() -> Path:
    """Locate config.toml: ~/.config/crude/ (XDG), then project root, then CWD."""
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    xdg_candidate = Path(xdg) / "crude" / "config.toml"
    if xdg_candidate.exists():
        return xdg_candidate
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config.toml"
        if candidate.exists():
            return candidate
    cwd_candidate = Path.cwd() / "config.toml"
    if cwd_candidate.exists():
        return cwd_candidate
    typer.echo(
        "Error: config.toml not found. Expected at ~/.config/crude/config.toml, project root, or CWD.",
        err=True,
    )
    raise typer.Exit(1)


def read_config(config_path: Path) -> dict:
    if sys.version_info >= (3, 11):
        import tomllib
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    else:
        import tomli
        with open(config_path, "rb") as f:
            return tomli.load(f)


def write_config(config_path: Path, config: dict) -> None:
    """Write the whole config back atomically (temp file in the same dir, os.replace).

    For rare, user-initiated config mutations (e.g. pinning a default tenant). It
    is not for hot-path token rotation, which lives in its own durable side file.
    """
    import tempfile

    import tomli_w

    config_path = Path(config_path)
    fd, tmp = tempfile.mkstemp(dir=str(config_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            tomli_w.dump(config, f)
        os.replace(tmp, config_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def resolve_account(config: dict, site: str, name: Optional[str]) -> dict:
    """Return the credential fields for one account of ``site``.

    A site section holds the default account as scalar keys and named accounts as
    subtables, so a config can carry several accounts on the same site (different
    countries, timezones, credentials)::

        [rezdy]            # default account
        api_key = "AU-key"

        [rezdy.es]         # named account "es"
        api_key = "ES-key"

    With ``name`` None the default account is returned: the section's scalar keys
    only (subtables stripped out), so an existing flat config is unchanged. With a
    name, the matching subtable is returned. A scalar key is a default-account
    field, never an account, so an account name that collides with a field name
    (rezdy ``api_key``, deputy ``deputy_install``, ...) is simply not found.
    """
    section = config.get(site, {})
    named = {k: v for k, v in section.items() if isinstance(v, dict)}
    if name is None:
        return {k: v for k, v in section.items() if not isinstance(v, dict)}
    if name not in named:
        available = ", ".join(sorted(named)) or "(none defined)"
        typer.echo(
            f"Error: no account named '{name}' under [{site}]. "
            f"Named accounts: {available}.",
            err=True,
        )
        raise typer.Exit(1)
    return named[name]


def s(value) -> str:
    """Coerce a field value to str, treating None and Odoo's False sentinel as empty."""
    if value is None or value is False:
        return ""
    return str(value)
