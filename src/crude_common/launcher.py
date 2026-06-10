"""The crude umbrella command: an index of the per-site CLIs.

crude has no resources of its own. Run with no arguments it lists the site
commands (crude-atdw, crude-skal, crude-rezdy, crude-deputy, crude-sonas) and the shared flags;
each site is a binary driven directly. It carries the same --version, --help, and
install-claude-command surface as the site CLIs.
"""

from __future__ import annotations

import typer

from crude_common.claude_command import (
    VERSION_HELP,
    add_install_command,
    refresh,
    version_callback,
)

app = typer.Typer(
    help="crude — index of the per-site CLIs (crude-atdw, crude-skal, crude-rezdy, crude-deputy, crude-sonas).",
)

# (binary, one-line description) for the no-argument listing. This is the index
# surface's own home; the site help strings, the README, and the Claude command
# each describe the sites for their own audiences.
SITES = [
    ("crude-atdw", "ATDW tourism listings (atdw-online.com.au)"),
    ("crude-skal", "Skal Australia member portal (australia.skal.org)"),
    ("crude-rezdy", "Rezdy products, availability, bookings (rezdy.com)"),
    ("crude-deputy", "Deputy rostering, timesheets, leave (deputy.com)"),
    ("crude-sonas", "Sonas wedding-venue software (app.sonas.events)"),
]


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        None, "--version", callback=version_callback, is_eager=True, help=VERSION_HELP
    ),
):
    refresh()
    if ctx.invoked_subcommand is not None:
        return
    typer.echo("crude — per-site CLIs:\n")
    for name, desc in SITES:
        typer.echo(f"  {name:<12} {desc}")
    typer.echo("\nRun a site command for usage, e.g. `crude-rezdy --help`.")
    typer.echo("Flags: --version, --help, install-claude-command")


add_install_command(app)


if __name__ == "__main__":
    app()
