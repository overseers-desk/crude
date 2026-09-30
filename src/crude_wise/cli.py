"""Typer CLI root for Wise Business accounts: crude-wise.

The credential is a personal API token from the ``[wise]`` config section, sent
as a static bearer, so there is no login step: the root wires the shared
--version/--account/install surface and builds a WiseSession per invocation.
"""

from __future__ import annotations

import typer

from crude_common.claude_command import register_claude_command
from crude_common.config import account, find_config, read_config, resolve_account
from crude_wise.client import WiseSession, base_url

app = typer.Typer(
    help="crude-wise — Wise Business balances, statements, transfers and recipients.",
    # A traceback must not print frame locals: the session holds the api_token.
    pretty_exceptions_show_locals=False,
)

register_claude_command(app)


def _make_client(config: dict) -> WiseSession:
    """Build a WiseSession from a parsed config dict for the selected account."""
    cfg = resolve_account(config, "wise", account())
    which = f"[wise.{account()}]" if account() else "[wise]"
    if not cfg.get("api_token"):
        typer.echo(f"Error: {which} must set api_token.", err=True)
        raise typer.Exit(1)
    return WiseSession(
        cfg["api_token"],
        base=base_url(cfg.get("environment")),
        profile_id=cfg.get("profile_id"),
        private_key=cfg.get("private_key"),
        account=account(),
    )


def _session() -> WiseSession:
    """The configured Wise session, reading the on-disk config."""
    return _make_client(read_config(find_config()))


from crude_wise import cli_resources, cli_status  # noqa: E402

cli_status.register(app)
app.add_typer(cli_resources.profile, name="profile")
app.add_typer(cli_resources.balance, name="balance")
app.add_typer(cli_resources.transaction, name="transaction")
app.add_typer(cli_resources.transfer, name="transfer")
app.add_typer(cli_resources.recipient, name="recipient")
app.add_typer(cli_resources.activity, name="activity")


if __name__ == "__main__":
    app()
