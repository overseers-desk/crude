"""Typer CLI root for a Mautic instance: crude-mautic.

The credential is a username and password from the ``[mautic]`` config section,
sent as HTTP basic auth, so there is no login step: the root wires the shared
--version/--account/install surface and builds a MauticSession per invocation.
The resource groups (``form``, ``contact``, ``segment``, ``campaign``, ``email``)
and the ``status`` command attach directly on the root, giving the
``crude-mautic <resource> <verb>`` grammar.
"""

from __future__ import annotations

import typer

from crude_common.claude_command import register_claude_command
from crude_common.config import account, find_config, read_config, resolve_account
from crude_mautic.client import MauticSession

app = typer.Typer(
    help="crude-mautic — Mautic forms, submissions, contacts, segments and campaigns.",
    # A traceback must not print frame locals: they hold the config's secrets.
    pretty_exceptions_show_locals=False,
)

register_claude_command(app)


def _make_client(config: dict) -> MauticSession:
    """Build a MauticSession from a parsed config dict for the selected account."""
    cfg = resolve_account(config, "mautic", account())
    which = f"[mautic.{account()}]" if account() else "[mautic]"
    missing = [k for k in ("base_url", "username", "password") if not cfg.get(k)]
    if missing:
        typer.echo(
            f"Error: {which} must set {', '.join(missing)}.", err=True)
        raise typer.Exit(1)
    return MauticSession(cfg["base_url"], cfg["username"], cfg["password"])


def _session() -> MauticSession:
    """The configured Mautic session, reading the on-disk config."""
    return _make_client(read_config(find_config()))


from crude_mautic import cli_resources, cli_status  # noqa: E402

cli_status.register(app)
app.add_typer(cli_resources.form, name="form")
app.add_typer(cli_resources.contact, name="contact")
app.add_typer(cli_resources.segment, name="segment")
app.add_typer(cli_resources.campaign, name="campaign")
app.add_typer(cli_resources.email, name="email")


if __name__ == "__main__":
    app()
