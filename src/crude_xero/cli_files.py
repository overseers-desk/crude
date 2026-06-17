"""Files API resource sub-apps for crude-xero.

`register(app)` attaches the `file`, `folder`, `association`, and `inbox`
sub-Typers, mirroring the Accounting CLI: a small `_resource` builder carries the
uniform CRUD verbs (list/get/create/update/delete), and the irregular verbs are
added explicitly — file `upload` (multipart) and `content` (raw-byte download),
the association list-by-file/list-by-object/add/remove, and the read-only inbox.
Reads render with the shared `_emit_list`/`_emit_record`; writes go through
`_do_write`/`_merge_update`, with confirm-before-write; bytes via `_emit_bytes`.

The builder binds to `_client().files`, so it is reproduced here rather than
imported: cli_accounting's `_resource` is hardwired to `_client().accounting`.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Optional

import typer

from crude_common.cliutil import _do_write, _emit_list, _emit_record, _merge_update, _read_data
from crude_xero.cli_accounting import _emit_bytes, _list_hint

# Shared columns for the two association list views (by file, by object).
_ASSOC_COLUMNS = [("File", "FileId"), ("Object", "ObjectId"),
                  ("Group", "ObjectGroup"), ("Type", "ObjectType")]


def _client(*args, **kwargs):
    """The configured Xero client (lazily, to avoid an import cycle with cli)."""
    from crude_xero.cli import _client as _impl

    return _impl(*args, **kwargs)


def _a(label: str) -> str:
    """The indefinite article for a resource label, for readable help text."""
    return "an" if label[:1].lower() in "aeiou" else "a"


# ----------------------------------------------------------------------
# Generic CRUD builder (bound to `.files`)
# ----------------------------------------------------------------------


def _resource(
    app: typer.Typer,
    name: str,
    label: str,
    columns: list,
    *,
    list_fn: Optional[str] = None,
    get_fn: Optional[str] = None,
    create_fn: Optional[str] = None,
    update_fn: Optional[str] = None,
    delete_fn: Optional[str] = None,
) -> typer.Typer:
    """Create a Files resource sub-app with the standard CRUD verbs and return it.

    The shape mirrors cli_accounting._resource; it lives here because that one is
    hardwired to `_client().accounting`. Files collections take no where/order
    filter, so `list` is the plain paged form. Irregular verbs are added to the
    returned sub-app by the caller.
    """
    sub = typer.Typer(help=f"Xero {label}.")
    app.add_typer(sub, name=name)

    if list_fn:

        @sub.command("list", help=f"List {label}.")
        def _list(
            fetch_all: bool = typer.Option(False, "--all", help="Fetch every page (default: the first page only)."),
            limit: Optional[int] = typer.Option(None, "--limit", help="Fetch up to N records across pages (--all wins)."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
        ):
            try:
                items = getattr(_client().files, list_fn)(
                    all_pages=fetch_all, limit=None if fetch_all else limit)
            except Exception as e:
                typer.echo(f"Error fetching {label}: {e}", err=True)
                raise typer.Exit(1)
            _list_hint(items, fetch_all, limit)
            _emit_list(items, columns, name, output_json)

    if get_fn:

        @sub.command("get", help=f"Show a single {label}.")
        def _get(
            guid: str = typer.Argument(..., help=f"{label} id (GUID)."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
        ):
            try:
                item = getattr(_client().files, get_fn)(guid)
            except Exception as e:
                typer.echo(f"Error fetching {label} {guid}: {e}", err=True)
                raise typer.Exit(1)
            _emit_record(item, output_json)

    if create_fn:

        @sub.command("create", help=f"Create {_a(label)} {label} from a JSON body.")
        def _create(
            data: Optional[str] = typer.Option(None, "--data", help=f"{label} object as JSON (or -f / stdin)."),
            file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
            yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
        ):
            body = _read_data(data, file)
            _do_write(
                lambda: getattr(_client().files, create_fn)(body),
                f"create {name}",
                confirm=f"Create this {name}?",
                yes=yes,
                output_json=output_json,
            )

    if update_fn:

        @sub.command("update", help=f"Update {_a(label)} {label} (read-merge-write).")
        def _update(
            guid: str = typer.Argument(..., help=f"{label} id (GUID) to update."),
            data: Optional[str] = typer.Option(None, "--data", help="Partial JSON overlaying the fetched record."),
            file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON overlay from a file."),
            yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
        ):
            client = _client().files
            _merge_update(
                lambda: getattr(client, get_fn)(guid),
                lambda merged: getattr(client, update_fn)(guid, merged),
                data,
                file,
                {},
                f"update {name} {guid}",
                yes,
                output_json,
            )

    if delete_fn:

        @sub.command("delete", help=f"Delete {_a(label)} {label} by id.")
        def _delete(
            guid: str = typer.Argument(..., help=f"{label} id (GUID)."),
            yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
        ):
            _do_write(
                lambda: getattr(_client().files, delete_fn)(guid),
                f"delete {name} {guid}",
                confirm=f"Delete {name} {guid}?",
                yes=yes,
                output_json=output_json,
            )

    return sub


# ----------------------------------------------------------------------
# register
# ----------------------------------------------------------------------


def register(app: typer.Typer) -> None:
    """Attach the Files resource sub-apps to the root app."""

    file = _resource(
        app, "file", "file",
        [("ID", "Id"), ("Name", "Name"), ("Mime", "MimeType"),
         ("Size", "Size"), ("Folder", "FolderId")],
        list_fn="list_files", get_fn="get_file",
        update_fn="update_file", delete_fn="delete_file",
    )

    @file.command("upload", help="Upload a file (multipart/form-data).")
    def _file_upload(
        file_path: str = typer.Option(..., "--file", help="Path to the file to upload."),
        name: Optional[str] = typer.Option(None, "--name", help="Stored name; defaults to the file's basename."),
        folder: Optional[str] = typer.Option(None, "--folder", help="Target folder id (GUID); default the root."),
        mime: Optional[str] = typer.Option(None, "--mime", help="MIME type; guessed from the filename if omitted."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        content = Path(file_path).read_bytes()
        stored = name or os.path.basename(file_path)
        ct = mime or mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        _do_write(
            lambda: _client().files.upload_file(stored, content, ct, folder_id=folder),
            f"upload {stored}", yes=yes, output_json=output_json,
        )

    @file.command("content", help="Download a file's content (to --out, else stdout).")
    def _file_content(
        guid: str = typer.Argument(..., help="File id (GUID)."),
        out: Optional[str] = typer.Option(None, "--out", help="Write the content to this path."),
    ):
        try:
            content = _client().files.get_file_content(guid)
        except Exception as e:
            typer.echo(f"Error fetching file content: {e}", err=True)
            raise typer.Exit(1)
        _emit_bytes(content, out, f"file {guid} content")

    _resource(
        app, "folder", "folder",
        [("ID", "Id"), ("Name", "Name"), ("Files", "FileCount"),
         ("Inbox", "IsInbox"), ("Email", "Email")],
        list_fn="list_folders", get_fn="get_folder",
        create_fn="create_folder", update_fn="update_folder", delete_fn="delete_folder",
    )

    _register_associations(app)
    _register_inbox(app)


def _register_associations(app: typer.Typer) -> None:
    """Attach the `association` sub-app: list by file or by object, add, remove."""
    association = typer.Typer(help="Xero file associations (file <-> accounting object).")
    app.add_typer(association, name="association")

    @association.command("list", help="List a file's associations.")
    def _assoc_list(
        file_id: str = typer.Argument(..., help="File id (GUID)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            items = _client().files.list_file_associations(file_id)
        except Exception as e:
            typer.echo(f"Error fetching associations: {e}", err=True)
            raise typer.Exit(1)
        _emit_list(items, _ASSOC_COLUMNS, "association", output_json)

    @association.command("object", help="List the files associated with an object.")
    def _assoc_object(
        object_id: str = typer.Argument(..., help="Accounting object id (GUID)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            items = _client().files.list_object_associations(object_id)
        except Exception as e:
            typer.echo(f"Error fetching associations: {e}", err=True)
            raise typer.Exit(1)
        _emit_list(items, _ASSOC_COLUMNS, "association", output_json)

    @association.command("add", help="Associate a file with an object (JSON: ObjectId, ObjectType, ObjectGroup).")
    def _assoc_add(
        file_id: str = typer.Argument(..., help="File id (GUID)."),
        data: Optional[str] = typer.Option(None, "--data", help="Association as JSON (or -f / stdin)."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = _read_data(data, file)
        _do_write(
            lambda: _client().files.create_association(file_id, body),
            f"associate file {file_id}", yes=yes, output_json=output_json,
        )

    @association.command("remove", help="Remove a file's association with an object.")
    def _assoc_remove(
        file_id: str = typer.Argument(..., help="File id (GUID)."),
        object_id: str = typer.Argument(..., help="Object id (GUID) to disassociate."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        _do_write(
            lambda: _client().files.delete_association(file_id, object_id),
            f"remove association {object_id} from file {file_id}",
            confirm=f"Remove association {object_id} from file {file_id}?",
            yes=yes, output_json=output_json,
        )


def _register_inbox(app: typer.Typer) -> None:
    """Attach the read-only `inbox` sub-app (Xero's default drop folder)."""
    inbox = typer.Typer(help="Xero inbox (read-only drop folder).")
    app.add_typer(inbox, name="inbox")

    @inbox.command("get", help="Show the inbox folder.")
    def _inbox_get(
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            item = _client().files.list_inbox()
        except Exception as e:
            typer.echo(f"Error fetching inbox: {e}", err=True)
            raise typer.Exit(1)
        _emit_record(item, output_json)
