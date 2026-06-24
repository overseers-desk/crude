"""Typer CLI root for the Meta Graph API: crude-meta.

The token is a static bearer read from the ``[meta]`` config section, so there is
no login step: the root wires the shared --version/--account/install surface, and
``_session`` builds a MetaSession that resolves the Page token and the Page/
Instagram ids at runtime. The platform sub-apps (``instagram``, ``facebook``) and
the account/status commands are attached at the foot of the file.
"""

from __future__ import annotations

import typer

from crude_common.claude_command import register_claude_command
from crude_common.config import account, find_config, read_config, resolve_account
from crude_meta.client import MetaSession

app = typer.Typer(
    help="crude-meta — Facebook Pages and Instagram over the Meta Graph API.",
)

register_claude_command(app)


def _make_client(config: dict) -> MetaSession:
    """Build a MetaSession from a parsed config dict for the selected account.

    Separate from _session so the live tests can construct a client from the
    crude_config fixture, the same way every other site CLI does.
    """
    meta = resolve_account(config, "meta", account())
    token = meta.get("access_token")
    if not token:
        which = f"[meta.{account()}]" if account() else "[meta]"
        typer.echo(f"Error: {which} must set access_token.", err=True)
        raise typer.Exit(1)
    return MetaSession(
        token,
        app_secret=meta.get("app_secret"),
        page_id=meta.get("page_id"),
        ig_user_id=meta.get("ig_user_id"),
    )


def _session() -> MetaSession:
    """The configured Meta session, reading the on-disk config."""
    return _make_client(read_config(find_config()))


from crude_meta import cli_facebook, cli_instagram, cli_status  # noqa: E402

cli_status.register(app)
app.add_typer(cli_instagram.app, name="instagram")
app.add_typer(cli_facebook.app, name="facebook")


if __name__ == "__main__":
    app()
