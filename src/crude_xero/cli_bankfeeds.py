"""Bank Feeds API resource sub-apps for crude-xero.

`register(app)` attaches two sub-`Typer`s: `feed-connection` (list/get/create/
delete) and `statement` (list/get/create). Reads render through the shared
`emit_list`/`emit_record`; writes go through `do_write` with confirm-before-
write. Two BankFeeds shapes show through to the CLI: the create and delete bodies
are batch envelopes (``{"items": [{...}]}``), passed through as the user supplies
them, and a feed connection is deleted by POSTing a delete request (the `delete`
verb calls `delete_feed_connections`, not an HTTP DELETE). The BankFeeds product
is reached through the `XeroClient.bankfeeds` facade group, like cli_projects
reaches `.projects`.
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_common.output import emit_list, emit_record
from crude_common.writeio import do_write, read_data


def _client(*args, **kwargs):
    """The configured Xero client (lazily, to avoid an import cycle with cli)."""
    from crude_xero.cli import _client as _impl

    return _impl(*args, **kwargs)


def _bankfeeds(*args, **kwargs):
    """The BankFeeds method group off the configured client facade (`.bankfeeds`)."""
    return _client(*args, **kwargs).bankfeeds


# Table columns per resource (BankFeeds fields are lower-camelCase).
_FEED_CONNECTION_COLS = [
    ("ID", "id"), ("AccountToken", "accountToken"), ("AccountNumber", "accountNumber"),
    ("Name", "accountName"), ("Type", "accountType"), ("Currency", "currency"),
    ("Status", "status"),
]
_STATEMENT_COLS = [
    ("ID", "id"), ("FeedConn", "feedConnectionId"), ("Status", "status"),
    ("Start", "startDate"), ("End", "endDate"),
    ("StartBal", "startBalance"), ("EndBal", "endBalance"),
]


# ----------------------------------------------------------------------
# register
# ----------------------------------------------------------------------


def register(app: typer.Typer) -> None:
    """Attach the BankFeeds resource sub-apps to the root app."""
    _register_feed_connection(app)
    _register_statement(app)


def _register_feed_connection(app: typer.Typer) -> None:
    feed = typer.Typer(help="Xero bank-feed connections (partner-application gated).")
    app.add_typer(feed, name="feed-connection")

    @feed.command("list", help="List feed connections.")
    def _list(
        page: Optional[int] = typer.Option(None, "--page", help="Page number (1-based)."),
        page_size: Optional[int] = typer.Option(None, "--page-size", help="Records per page."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            items = _bankfeeds().list_feed_connections(page=page, page_size=page_size)
        except Exception as e:
            typer.echo(f"Error fetching feed connections: {e}", err=True)
            raise typer.Exit(1)
        emit_list(items, _FEED_CONNECTION_COLS, "feed connection", output_json)

    @feed.command("get", help="Show a single feed connection.")
    def _get(
        feed_connection_id: str = typer.Argument(..., help="Feed connection id (GUID)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            item = _bankfeeds().get_feed_connection(feed_connection_id)
        except Exception as e:
            typer.echo(f"Error fetching feed connection {feed_connection_id}: {e}", err=True)
            raise typer.Exit(1)
        emit_record(item, output_json)

    @feed.command("create", help='Create feed connection(s) from an items-wrapped JSON body ({"items":[{...}]}).')
    def _create(
        data: Optional[str] = typer.Option(None, "--data", help='Batch body {"items":[{...}]} as JSON (or -f / stdin).'),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = read_data(data, file)
        do_write(
            lambda: _bankfeeds().create_feed_connections(body),
            "create feed connection(s)", confirm="Create these feed connection(s)?",
            yes=yes, output_json=output_json,
        )

    @feed.command(
        "delete",
        help='Delete feed connection(s) via a POSTed delete-request batch ({"items":[{...}]}); BankFeeds has no HTTP DELETE.',
    )
    def _delete(
        data: Optional[str] = typer.Option(None, "--data", help='Delete-request batch {"items":[{...}]} as JSON (or -f / stdin).'),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = read_data(data, file)
        do_write(
            lambda: _bankfeeds().delete_feed_connections(body),
            "delete feed connection(s)", confirm="Delete these feed connection(s)?",
            yes=yes, output_json=output_json,
        )


def _register_statement(app: typer.Typer) -> None:
    statement = typer.Typer(help="Xero bank-feed statements (partner-application gated).")
    app.add_typer(statement, name="statement")

    @statement.command("list", help="List statements.")
    def _list(
        page: Optional[int] = typer.Option(None, "--page", help="Page number (1-based)."),
        page_size: Optional[int] = typer.Option(None, "--page-size", help="Records per page."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            items = _bankfeeds().list_statements(page=page, page_size=page_size)
        except Exception as e:
            typer.echo(f"Error fetching statements: {e}", err=True)
            raise typer.Exit(1)
        emit_list(items, _STATEMENT_COLS, "statement", output_json)

    @statement.command("get", help="Show a single statement.")
    def _get(
        statement_id: str = typer.Argument(..., help="Statement id (GUID)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            item = _bankfeeds().get_statement(statement_id)
        except Exception as e:
            typer.echo(f"Error fetching statement {statement_id}: {e}", err=True)
            raise typer.Exit(1)
        emit_record(item, output_json)

    @statement.command("create", help='Create statement(s) from an items-wrapped JSON body ({"items":[{...}]}).')
    def _create(
        data: Optional[str] = typer.Option(None, "--data", help='Batch body {"items":[{...}]} as JSON (or -f / stdin).'),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = read_data(data, file)
        do_write(
            lambda: _bankfeeds().create_statements(body),
            "create statement(s)", confirm="Create these statement(s)?",
            yes=yes, output_json=output_json,
        )
