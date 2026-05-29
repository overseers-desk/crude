"""Install and keep current the crude command for Claude Code.

This installs a Claude Code *command* (``~/.claude/commands/crude.md``), not a
skill. A user's skills directory is frequently a version-controlled, curated
collection, so a CLI writing into it would pollute that repository; the commands
directory is the conventional home for a tool to register itself. The ``COMMAND``
text below is the single source for the command's content. Each site CLI keeps
the installed file equal to it: on every run, when the file is missing or differs
from ``COMMAND``, it is rewritten. "Current" means byte-for-byte equal to
``COMMAND``, so there is no version stamp to maintain and no per-release judgement
about whether the command changed. A same-named skill, if the user keeps one,
supersedes the command and the refresh leaves it alone.
"""

from __future__ import annotations

from pathlib import Path

import typer

from crude_common import version as crude_version

COMMAND_NAME = "crude"

VERSION_HELP = "Show the crude version and exit."

# The command body and the single source of its content. The description lists
# the sites crude supports and stays short, so an agent reaches for it when those
# sites come up; how to drive the CLIs is the body below, not the description.
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
    crude-rezdy booking list [--status] [--search] [--product] [--from] [--to] [--created-from] [--created-to] [--updated-from] [--updated-to] [--limit] [--offset] [--all]
    crude-rezdy booking cancellations [--from <YYYY-MM-DD>] [--to <YYYY-MM-DD>] [--limit] [--all]
    crude-rezdy booking get <orderno>

`booking cancellations` filters by when the cancellation occurred (dateUpdated), not the session date. Use --from/--to with YYYY-MM-DD dates.
--updated-from / --updated-to on `booking list` apply the same client-side filter to any status.
--all on either command fetches all pages automatically (default limit is otherwise applied).
For one day's bookings, set --from and --to to that day's bounds. Availability times are local (`YYYY-MM-DD HH:mm:ss`); booking times are ISO 8601.
"""


def command_file() -> Path:
    return Path.home() / ".claude" / "commands" / f"{COMMAND_NAME}.md"


def skill_dir() -> Path:
    """A skill of the same name, if the user keeps one, supersedes the command."""
    return Path.home() / ".claude" / "skills" / COMMAND_NAME


def _superseded() -> bool:
    """True when Claude Code is absent, or a same-named skill supersedes the command."""
    return not (Path.home() / ".claude").exists() or skill_dir().exists()


def refresh() -> None:
    """Rewrite the command file when it is missing or differs from COMMAND.

    Idempotent and silent. Does nothing when Claude Code is not installed or a
    same-named skill supersedes the command. "Out of date" is content inequality
    with COMMAND, so no version field is needed.
    """
    if _superseded():
        return
    f = command_file()
    if f.exists() and f.read_text() == COMMAND:
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(COMMAND)


def version_callback(value: bool) -> None:
    """Eager ``--version`` handler shared by every crude CLI: print and exit.

    Wired into each app's root callback so ``crude`` and the site binaries all
    report the same number, sourced once from package metadata.
    """
    if value:
        typer.echo(crude_version())
        raise typer.Exit()


def add_install_command(app) -> None:
    """Attach the install-claude-command subcommand to ``app``.

    Separate from the root callback so the crude umbrella, which needs its own
    callback to list the site commands, can still register the subcommand.
    """

    @app.command("install-claude-command")
    def install_claude_command():
        """(Re)write the crude command for Claude Code."""
        if _superseded():
            typer.echo(
                "Skipped: Claude Code is not installed, or a same-named skill "
                "supersedes the command."
            )
            return
        f = command_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(COMMAND)
        typer.echo(f"Installed: {f}")


def register_claude_command(app) -> None:
    """Attach the shared root callback and the install-claude-command subcommand.

    The root callback keeps ``~/.claude/commands/crude.md`` equal to COMMAND on
    every invocation and handles the shared ``--version`` flag; the subcommand does
    the same write explicitly, with feedback. Used by the site CLIs; the crude
    umbrella wires its own callback and calls ``add_install_command`` directly.
    """

    @app.callback()
    def _root(
        version: bool = typer.Option(
            None, "--version", callback=version_callback, is_eager=True, help=VERSION_HELP
        ),
    ):
        refresh()

    add_install_command(app)
