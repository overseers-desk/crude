"""Install and freshness-check the crude command for Claude Code.

This installs a Claude Code *command* (``~/.claude/commands/crude.md``), not a
skill. A user's skills directory is frequently a version-controlled, curated
collection, so a CLI writing into it would pollute that repository; the
commands directory is the conventional home for a tool to register itself.
One command, covering every site crude supports, is written with a ``version:``
stamp in its frontmatter. Each site CLI registers an ``install-claude-command``
subcommand that writes it, and a startup nudge that points the agent at that
subcommand when the command is missing or its stamp differs from the running
tool. A same-named skill, if the user has one, supersedes the command and
silences the nudge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

COMMAND_NAME = "crude"

# The command body. The description lists the sites crude supports and stays
# short, so an agent reaches for it when those sites come up; how to drive the
# CLIs is the body below, not the description. render() splices the version in.
COMMAND = """---
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


def command_file() -> Path:
    return Path.home() / ".claude" / "commands" / f"{COMMAND_NAME}.md"


def skill_dir() -> Path:
    """A skill of the same name, if the user keeps one, supersedes the command."""
    return Path.home() / ".claude" / "skills" / COMMAND_NAME


def render(version: str) -> str:
    """Return the command text with a version stamp as the first frontmatter field."""
    if COMMAND.startswith("---\n"):
        return "---\nversion: " + version + "\n" + COMMAND[4:]
    return COMMAND


def installed_version() -> Optional[str]:
    """Return the version stamped in the installed command, or None."""
    f = command_file()
    if not f.exists():
        return None
    for line in f.read_text().splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    return None


def registration_status(version: str, prog: str) -> Optional[str]:
    """Return an agent-directed nudge if the command is missing or stale, else None.

    Silent when Claude Code is not installed (no ~/.claude) or when a same-named
    skill supersedes the command. Staleness is an exact stamp mismatch against
    the running tool's version.
    """
    if not (Path.home() / ".claude").exists():
        return None
    if skill_dir().exists():
        return None
    if not command_file().exists():
        return (
            "crude command not installed in Claude Code. "
            f"Run `{prog} install-claude-command` to install it, "
            "then continue with the user's request."
        )
    installed = installed_version()
    if installed != version:
        label = installed if installed else "unknown"
        return (
            f"crude command is at version {label}, tool is {version}. "
            f"Run `{prog} install-claude-command` to update it, "
            "then continue with the user's request."
        )
    return None


def register_claude_command(app, prog: str) -> None:
    """Attach the freshness nudge and the install-claude-command subcommand to a CLI.

    Every site CLI registers the same pair: a root callback that warns (on stderr)
    when the installed Claude Code command is missing or stale, and an
    ``install-claude-command`` subcommand that (re)writes it. Only ``prog`` (the
    binary name, e.g. ``crude-atdw``) differs between sites.
    """
    from crude_common import __version__ as version

    @app.callback()
    def _root(ctx: typer.Context):
        if ctx.invoked_subcommand != "install-claude-command":
            nudge = registration_status(version, prog)
            if nudge:
                typer.echo(nudge, err=True)

    @app.command("install-claude-command")
    def install_claude_command():
        """Install or update the crude command for Claude Code."""
        run_install(version, prog)


def run_install(version: str, prog: str) -> None:
    """Write (or update) the command, prompting before replacing a different version."""
    f = command_file()
    f.parent.mkdir(parents=True, exist_ok=True)

    if f.exists():
        installed = installed_version()
        label = installed if installed else "unknown version"
        if installed == version:
            typer.echo(f"Already at {version}: {f}")
            return
        if not typer.confirm(f"crude command already installed ({label}). Replace with {version}?"):
            typer.echo("Aborted.")
            raise typer.Exit(1)

    f.write_text(render(version))
    typer.echo(f"Installed {version}: {f}")
