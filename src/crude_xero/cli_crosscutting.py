"""Cross-cutting attachment and history sub-apps for crude-xero.

Two generic sub-apps parameterised by the parent object type plus its GUID, rather
than ~75 per-resource verbs. `--on` names the parent type (a friendly singular),
validated against the attachment-capable or history-capable whitelist before any
client is built; an out-of-set value errors with the valid keys. The generic
methods live on `AccountingAPI`.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Optional

import typer

from crude_common.cliutil import _do_write, _emit_list
from crude_xero.accounting import ATTACHMENT_ENDPOINTS, HISTORY_ENDPOINTS
from crude_xero.cli_accounting import _client, _emit_bytes


def _check_endpoint(on: str, mapping: dict) -> None:
    """Validate --on against a whitelist, erroring with the valid keys on a miss."""
    if on not in mapping:
        valid = ", ".join(sorted(mapping))
        typer.echo(f"Error: '--on {on}' is not valid; choose one of: {valid}.", err=True)
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    """Attach the `attachment` and `history` sub-apps to the root app."""

    attachment = typer.Typer(help="Attachments on Accounting objects (generic, by --on/--id).")
    history = typer.Typer(help="History & notes on Accounting objects (generic, by --on/--id).")
    app.add_typer(attachment, name="attachment")
    app.add_typer(history, name="history")

    @attachment.command("list", help="List an object's attachments.")
    def _attachment_list(
        on: str = typer.Option(..., "--on", help="Parent object type (e.g. invoice, contact)."),
        id_: str = typer.Option(..., "--id", help="Parent object id (GUID)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        _check_endpoint(on, ATTACHMENT_ENDPOINTS)
        try:
            items = _client().accounting.list_attachments(on, id_)
        except Exception as e:
            typer.echo(f"Error fetching attachments: {e}", err=True)
            raise typer.Exit(1)
        _emit_list(
            items,
            [("ID", "AttachmentID"), ("File", "FileName"),
             ("Mime", "MimeType"), ("Size", "ContentLength")],
            "attachment", output_json,
        )

    @attachment.command("get", help="Download an attachment (to --out, else stdout).")
    def _attachment_get(
        on: str = typer.Option(..., "--on", help="Parent object type (e.g. invoice, contact)."),
        id_: str = typer.Option(..., "--id", help="Parent object id (GUID)."),
        file: str = typer.Option(..., "--file", help="Attachment file id or filename."),
        out: Optional[str] = typer.Option(None, "--out", help="Write the attachment to this path."),
    ):
        _check_endpoint(on, ATTACHMENT_ENDPOINTS)
        try:
            content = _client().accounting.get_attachment(on, id_, file)
        except Exception as e:
            typer.echo(f"Error fetching attachment: {e}", err=True)
            raise typer.Exit(1)
        _emit_bytes(content, out, f"{on} {id_} attachment {file}")

    @attachment.command("add", help="Upload a file as an attachment.")
    def _attachment_add(
        on: str = typer.Option(..., "--on", help="Parent object type (e.g. invoice, contact)."),
        id_: str = typer.Option(..., "--id", help="Parent object id (GUID)."),
        file: str = typer.Option(..., "--file", help="Path to the file to upload."),
        mime: Optional[str] = typer.Option(None, "--mime", help="MIME type; guessed from the filename if omitted."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        _check_endpoint(on, ATTACHMENT_ENDPOINTS)
        content = Path(file).read_bytes()
        filename = os.path.basename(file)
        ct = mime or mimetypes.guess_type(file)[0] or "application/octet-stream"
        _do_write(
            lambda: _client().accounting.add_attachment(on, id_, filename, content, ct),
            f"add attachment {filename} to {on} {id_}", yes=yes, output_json=output_json,
        )

    @history.command("list", help="List an object's history & notes.")
    def _history_list(
        on: str = typer.Option(..., "--on", help="Parent object type (e.g. invoice, payment)."),
        id_: str = typer.Option(..., "--id", help="Parent object id (GUID)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        _check_endpoint(on, HISTORY_ENDPOINTS)
        try:
            items = _client().accounting.list_history(on, id_)
        except Exception as e:
            typer.echo(f"Error fetching history: {e}", err=True)
            raise typer.Exit(1)
        _emit_list(
            items,
            [("Date", "DateUTC"), ("User", "User"), ("Details", "Details")],
            "history record", output_json,
        )

    @history.command("add", help="Add a note to an object's history.")
    def _history_add(
        on: str = typer.Option(..., "--on", help="Parent object type (e.g. invoice, payment)."),
        id_: str = typer.Option(..., "--id", help="Parent object id (GUID)."),
        note: str = typer.Option(..., "--note", help="The note text."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        _check_endpoint(on, HISTORY_ENDPOINTS)
        _do_write(
            lambda: _client().accounting.add_history(on, id_, note),
            f"add note to {on} {id_}", yes=yes, output_json=output_json,
        )
