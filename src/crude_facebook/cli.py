"""Typer CLI root for the Facebook Graph API: crude-facebook.

The token is a static bearer read from the ``[facebook]`` config section, so there
is no login step: the root wires the shared --version/--account/install surface,
and ``_session`` builds a FacebookSession that resolves the Page token and id at
runtime. The resource groups (``post``, ``comment``, ``page``) and the ``status``
command are attached directly on the root, giving the ``crude-facebook <resource>
<verb>`` grammar.
"""

from __future__ import annotations

import typer

from crude_common.claude_command import register_claude_command
from crude_common.config import account, find_config, read_config, resolve_account
from crude_facebook.client import FacebookSession

app = typer.Typer(
    help="crude-facebook — Facebook Page posts, insights, and comments over the Graph API.",
)

register_claude_command(app)


def _make_client(config: dict) -> FacebookSession:
    """Build a FacebookSession from a parsed config dict for the selected account.

    Separate from _session so the live tests can construct a client from the
    crude_config fixture, the same way every other site CLI does.
    """
    fb = resolve_account(config, "facebook", account())
    token = fb.get("access_token")
    if not token:
        which = f"[facebook.{account()}]" if account() else "[facebook]"
        typer.echo(f"Error: {which} must set access_token.", err=True)
        raise typer.Exit(1)
    return FacebookSession(
        token,
        app_secret=fb.get("app_secret"),
        page_id=fb.get("page_id"),
    )


def _session() -> FacebookSession:
    """The configured Facebook session, reading the on-disk config."""
    return _make_client(read_config(find_config()))


from crude_facebook import cli_resources, cli_status  # noqa: E402

cli_status.register(app)
app.add_typer(cli_resources.post, name="post")
app.add_typer(cli_resources.comment, name="comment")
app.add_typer(cli_resources.page, name="page")


if __name__ == "__main__":
    app()
