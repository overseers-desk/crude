"""Typer CLI root for the AP Clover REST API: crude-clover.

The token is a static Bearer issued once from the AP production dashboard, so
there is no login step and no auth module: the root wires the shared
--version/--account/install-claude-command surface, and `_client` reads the
token from the ``[clover]`` config section and resolves the merchant at runtime.
The resource sub-apps are attached by the ``cli_<group>.register(app)`` calls at
the foot of the file.
"""

from __future__ import annotations

import typer

from crude_common.claude_command import register_claude_command
from crude_common.config import account, find_config, read_config, resolve_account
from crude_clover.client import CloverClient, CloverSession

app = typer.Typer(
    help="crude-clover — AP Clover POS orders, catalog, and Square-shape export over the REST API.",
)

register_claude_command(app)


def _client() -> CloverClient:
    """The configured Clover client for the selected account."""
    clover = resolve_account(read_config(find_config()), "clover", account())
    token = clover.get("api_token")
    if not token:
        which = f"[clover.{account()}]" if account() else "[clover]"
        typer.echo(f"Error: {which} must set api_token.", err=True)
        raise typer.Exit(1)
    return CloverClient(CloverSession(token))


from crude_clover import cli_catalog, cli_flatten, cli_orders  # noqa: E402

cli_orders.register(app)
cli_catalog.register(app)
cli_flatten.register(app)


if __name__ == "__main__":
    app()
