"""Install and freshness-check the crude skill for Claude Code.

One skill, covering every site crude supports, is written to
``~/.claude/skills/crude/SKILL.md`` with a ``version:`` stamp in its
frontmatter. Each site CLI registers an ``install-claude-command`` subcommand
that writes it, and a startup nudge that points the agent at that subcommand
when the skill is missing or its stamp differs from the running tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

SKILL_NAME = "crude"

# The skill body. The description lists the sites crude supports and stays
# short, so an agent reaches for it when those sites come up; how to drive the
# CLIs is the body below, not the description. render() splices the version in.
SKILL = """---
name: crude
description: Read and edit your own data on atdw-online.com.au (ATDW tourism listings), australia.skal.org (Skal Australia member portal), and rezdy.com (products, availability, bookings).
allowed-tools: Bash
---

# crude

crude provides command-line clients for reading and editing your own data on sites that lack a usable public API. Each site is its own binary. Configuration for all of them lives in `~/.config/crude/config.toml` (sections `[atdw]`, `[skal]`, `[rezdy]`). Add `--json` to any read command for machine-readable output.

## crude-atdw (atdw-online.com.au)

Tourism listings. Credentials in `[atdw]`; the JWT token is cached and renewed automatically.

    crude-atdw login
    crude-atdw listing list [--scope own|all] [--type] [--city] [--state] [--status] [--name] [--limit] [--offset]
    crude-atdw listing get <id>
    crude-atdw listing update <id> <field> <value>
    crude-atdw listing submit <id>

`listing list` with no filters returns your own organisation's listings; any filter flag or `--scope all` searches every visible listing.

## crude-skal (australia.skal.org)

Skal Australia member portal. Credentials in `[skal]`; the session cookie lasts about 30 days and is cached automatically.

    crude-skal login
    crude-skal member list [--name] [--city] [--club <id>] [--email] [--state] [--limit] [--offset]
    crude-skal member get <id>
    crude-skal club list
    crude-skal event list [--limit]

Member `--state` values: active, draft, unpaid, done, club_change (default excludes done). Club IDs: 330 Melbourne, 334 Sydney, 322 Brisbane, 333 Perth, 321 Adelaide, 1003 Gold Coast (full list in the crude repo docs/skal-api.md).

## crude-rezdy (rezdy.com)

Rezdy Supplier API. API key in `[rezdy]` (`api_key`, optional `environment`); there is no login step.

    crude-rezdy product list [--search] [--limit] [--offset]
    crude-rezdy product get <code>
    crude-rezdy availability list --product <code> --from "<YYYY-MM-DD HH:mm:ss>" --to "<...>" [--min-availability] [--limit]
    crude-rezdy booking list [--status] [--search] [--product] [--from] [--to] [--created-from] [--created-to] [--limit] [--offset]
    crude-rezdy booking get <orderno>

For one day's bookings, set --from and --to to that day's bounds. Availability times are local (`YYYY-MM-DD HH:mm:ss`); booking times are ISO 8601.
"""


def skill_dir() -> Path:
    return Path.home() / ".claude" / "skills" / SKILL_NAME


def skill_file() -> Path:
    return skill_dir() / "SKILL.md"


def render(version: str) -> str:
    """Return the skill text with a version stamp as the first frontmatter field."""
    if SKILL.startswith("---\n"):
        return "---\nversion: " + version + "\n" + SKILL[4:]
    return SKILL


def installed_version() -> Optional[str]:
    """Return the version stamped in the installed skill, or None."""
    f = skill_file()
    if not f.exists():
        return None
    for line in f.read_text().splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    return None


def registration_status(version: str, prog: str) -> Optional[str]:
    """Return an agent-directed nudge if the skill is missing or stale, else None.

    Silent when Claude Code is not installed (no ~/.claude). Staleness is an
    exact stamp mismatch against the running tool's version.
    """
    if not (Path.home() / ".claude").exists():
        return None
    if not skill_file().exists():
        return (
            "crude skill not installed in Claude Code. "
            f"Run `{prog} install-claude-command` to install it, "
            "then continue with the user's request."
        )
    installed = installed_version()
    if installed != version:
        label = installed if installed else "unknown"
        return (
            f"crude skill is at version {label}, tool is {version}. "
            f"Run `{prog} install-claude-command` to update it, "
            "then continue with the user's request."
        )
    return None


def run_install(version: str, prog: str) -> None:
    """Write (or update) the skill, prompting before replacing a different version."""
    import typer

    d = skill_dir()
    f = skill_file()
    d.mkdir(parents=True, exist_ok=True)

    if f.exists():
        installed = installed_version()
        label = installed if installed else "unknown version"
        if installed == version:
            typer.echo(f"Already at {version}: {f}")
            return
        if not typer.confirm(f"crude skill already installed ({label}). Replace with {version}?"):
            typer.echo("Aborted.")
            raise typer.Exit(1)

    f.write_text(render(version))
    typer.echo(f"Installed {version}: {f}")
