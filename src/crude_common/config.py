"""Config discovery and reading shared across the crude site CLIs.

One config file (``~/.config/crude/config.toml``) holds a section per site, so
locating and parsing it is the same job for every CLI; it lives here rather than
being copied into each.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer


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


def s(value) -> str:
    """Coerce a field value to str, treating None and Odoo's False sentinel as empty."""
    if value is None or value is False:
        return ""
    return str(value)
