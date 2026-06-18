"""Accounting API resource sub-apps for crude-xero.

`register(app)` attaches one sub-`Typer` per Accounting resource, with the verbs
from the command-grammar table. The uniform CRUD verbs (list/get/create/update/
delete, soft-delete/archive, pdf) are built by `_resource`, keeping one home for
the shared pattern; the irregular verbs (allocate, email, online-url, members,
options, the named reports, the GUID-less tax-rate update) are added explicitly to
the returned sub-app. Reads render with the shared `emit_list`/`emit_record`;
writes go through `do_write`/`merge_update`, with confirm-before-write.
"""

from __future__ import annotations

import sys
from typing import List, Optional

import typer

from crude_common.output import emit_list, emit_record
from crude_common.writeio import do_write, merge_update, read_data
from crude_xero.accounting import REPORT_NAMES
from crude_xero.client import PAGE_SIZE


def _client(*args, **kwargs):
    """The configured Xero client (lazily, to avoid an import cycle with cli)."""
    from crude_xero.cli import _client as _impl

    return _impl(*args, **kwargs)


def _a(label: str) -> str:
    """The indefinite article for a resource label, for readable help text."""
    return "an" if label[:1].lower() in "aeiou" else "a"


def _list_hint(items: list, fetch_all: bool, limit: Optional[int]) -> None:
    """Warn (stderr) when a bare list came back a full page, so more likely exist."""
    if not fetch_all and limit is None and len(items) == PAGE_SIZE:
        typer.echo(
            f"Showing the first {PAGE_SIZE}; pass --all for all, or --limit N for more.",
            err=True,
        )


def _emit_bytes(content: bytes, out: Optional[str], what: str) -> None:
    """Write raw bytes (a PDF or attachment) to --out, else stdout."""
    if out:
        with open(out, "wb") as f:
            f.write(content)
        typer.echo(f"{what}: wrote {len(content)} bytes to {out}.")
    else:
        sys.stdout.buffer.write(content)


# ----------------------------------------------------------------------
# Generic verb builders
# ----------------------------------------------------------------------


def _add_remove(sub: typer.Typer, cmd: str, method: str, name: str, label: str) -> None:
    """Add a confirm-before-write remove verb (hard delete, soft delete, or archive)."""

    @sub.command(cmd, help=f"{cmd.capitalize()} {_a(label)} {label} by id.")
    def _remove(
        guid: str = typer.Argument(..., help=f"{label} id (GUID)."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        do_write(
            lambda: getattr(_client().accounting, method)(guid),
            f"{cmd} {name} {guid}",
            confirm=f"{cmd.capitalize()} {name} {guid}?",
            yes=yes,
            output_json=output_json,
        )


def _add_pdf(sub: typer.Typer, method: str, name: str, label: str) -> None:
    """Add a `pdf` verb that writes the PDF bytes to --out or stdout."""

    @sub.command("pdf", help=f"Download the {label} PDF (to --out, else stdout).")
    def _pdf(
        guid: str = typer.Argument(..., help=f"{label} id (GUID)."),
        out: Optional[str] = typer.Option(None, "--out", help="Write the PDF to this path."),
    ):
        try:
            content = getattr(_client().accounting, method)(guid)
        except Exception as e:
            typer.echo(f"Error fetching {name} PDF: {e}", err=True)
            raise typer.Exit(1)
        _emit_bytes(content, out, f"{name} {guid} PDF")


def _add_allocate(sub: typer.Typer, method: str, name: str, label: str) -> None:
    """Add an `allocate` verb posting an allocation (JSON body) to an invoice."""

    @sub.command("allocate", help=f"Allocate the {label} to an invoice (JSON body).")
    def _allocate(
        guid: str = typer.Argument(..., help=f"{label} id (GUID)."),
        data: Optional[str] = typer.Option(None, "--data", help="Allocation as JSON (or -f / stdin)."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = read_data(data, file)
        do_write(
            lambda: getattr(_client().accounting, method)(guid, body),
            f"allocate {name} {guid}",
            confirm=f"Allocate {name} {guid}?",
            yes=yes,
            output_json=output_json,
        )


def _resource(
    app: typer.Typer,
    name: str,
    label: str,
    columns: list,
    *,
    list_fn: Optional[str] = None,
    list_filters: bool = True,
    get_fn: Optional[str] = None,
    create_fn: Optional[str] = None,
    update_fn: Optional[str] = None,
    delete_fn: Optional[str] = None,
    remove: Optional[tuple] = None,
) -> typer.Typer:
    """Create a resource sub-app with the standard CRUD verbs and return it.

    `list_filters` False is for the read-only collections whose client method
    takes no where/order (currencies, branding themes, ...). `delete_fn` is a hard
    delete; `remove` is a (command_name, method_name) soft delete/archive. Both
    confirm. Irregular verbs are added to the returned sub-app by the caller.
    """
    sub = typer.Typer(help=f"Xero {label}.")
    app.add_typer(sub, name=name)

    if list_fn and list_filters:

        @sub.command("list", help=f"List {label}.")
        def _list(
            where: Optional[str] = typer.Option(None, "--where", help="Xero filter expression (where=)."),
            order: Optional[str] = typer.Option(None, "--order", help="Xero sort expression (order=)."),
            fetch_all: bool = typer.Option(False, "--all", help="Fetch every page (default: the first page only)."),
            limit: Optional[int] = typer.Option(None, "--limit", help="Fetch up to N records across pages (--all wins)."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
        ):
            try:
                items = getattr(_client().accounting, list_fn)(
                    where=where, order=order, all_pages=fetch_all,
                    limit=None if fetch_all else limit)
            except Exception as e:
                typer.echo(f"Error fetching {label}: {e}", err=True)
                raise typer.Exit(1)
            _list_hint(items, fetch_all, limit)
            emit_list(items, columns, name, output_json)

    elif list_fn:

        @sub.command("list", help=f"List {label}.")
        def _list_plain(
            fetch_all: bool = typer.Option(False, "--all", help="Fetch every page (default: the first page only)."),
            limit: Optional[int] = typer.Option(None, "--limit", help="Fetch up to N records across pages (--all wins)."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
        ):
            try:
                items = getattr(_client().accounting, list_fn)(
                    all_pages=fetch_all, limit=None if fetch_all else limit)
            except Exception as e:
                typer.echo(f"Error fetching {label}: {e}", err=True)
                raise typer.Exit(1)
            _list_hint(items, fetch_all, limit)
            emit_list(items, columns, name, output_json)

    if get_fn:

        @sub.command("get", help=f"Show a single {label}.")
        def _get(
            guid: str = typer.Argument(..., help=f"{label} id (GUID)."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
        ):
            try:
                item = getattr(_client().accounting, get_fn)(guid)
            except Exception as e:
                typer.echo(f"Error fetching {label} {guid}: {e}", err=True)
                raise typer.Exit(1)
            emit_record(item, output_json)

    if create_fn:

        @sub.command("create", help=f"Create {_a(label)} {label} from a JSON body.")
        def _create(
            data: Optional[str] = typer.Option(None, "--data", help=f"{label} object as JSON (or -f / stdin)."),
            file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
            yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
            output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
        ):
            body = read_data(data, file)
            do_write(
                lambda: getattr(_client().accounting, create_fn)(body),
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
            client = _client().accounting
            merge_update(
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
        _add_remove(sub, "delete", delete_fn, name, label)

    if remove:
        _add_remove(sub, remove[0], remove[1], name, label)

    return sub


def _contact(c: dict) -> str:
    return (c.get("Contact") or {}).get("Name") or ""


# ----------------------------------------------------------------------
# register
# ----------------------------------------------------------------------


def register(app: typer.Typer) -> None:
    """Attach the Accounting resource sub-apps to the root app."""

    _resource(
        app, "account", "account",
        [("ID", "AccountID"), ("Code", "Code"), ("Name", "Name"), ("Type", "Type"), ("Status", "Status")],
        list_fn="list_accounts", get_fn="get_account",
        create_fn="create_account", update_fn="update_account", delete_fn="delete_account",
    )

    _resource(
        app, "bank-transaction", "bank transaction",
        [("ID", "BankTransactionID"), ("Type", "Type"), ("Contact", _contact),
         ("Date", "Date"), ("Total", "Total"), ("Status", "Status")],
        list_fn="list_bank_transactions", get_fn="get_bank_transaction",
        create_fn="create_bank_transaction", update_fn="update_bank_transaction",
    )

    _resource(
        app, "bank-transfer", "bank transfer",
        [("ID", "BankTransferID"),
         ("From", lambda t: (t.get("FromBankAccount") or {}).get("Name")),
         ("To", lambda t: (t.get("ToBankAccount") or {}).get("Name")),
         ("Amount", "Amount"), ("Date", "Date")],
        list_fn="list_bank_transfers", get_fn="get_bank_transfer",
        create_fn="create_bank_transfer",
    )

    _resource(
        app, "batch-payment", "batch payment",
        [("ID", "BatchPaymentID"), ("Date", "Date"),
         ("Total", lambda b: b.get("TotalAmount") or b.get("Amount")), ("Status", "Status")],
        list_fn="list_batch_payments", get_fn="get_batch_payment",
        create_fn="create_batch_payment", remove=("delete", "delete_batch_payment"),
    )

    _resource(
        app, "branding-theme", "branding theme",
        [("ID", "BrandingThemeID"), ("Name", "Name"), ("Sort", "SortOrder")],
        list_fn="list_branding_themes", list_filters=False, get_fn="get_branding_theme",
    )

    _resource(
        app, "budget", "budget",
        [("ID", "BudgetID"), ("Type", "Type"), ("Description", "Description")],
        list_fn="list_budgets", list_filters=False, get_fn="get_budget",
    )

    _resource(
        app, "contact", "contact",
        [("ID", "ContactID"), ("Name", "Name"), ("First", "FirstName"),
         ("Last", "LastName"), ("Email", "EmailAddress"), ("Status", "ContactStatus")],
        list_fn="list_contacts", get_fn="get_contact",
        create_fn="create_contact", update_fn="update_contact",
        remove=("archive", "archive_contact"),
    )

    contact_group = _resource(
        app, "contact-group", "contact group",
        [("ID", "ContactGroupID"), ("Name", "Name"), ("Status", "Status"),
         ("Contacts", lambda g: len(g.get("Contacts") or []))],
        list_fn="list_contact_groups", get_fn="get_contact_group",
        create_fn="create_contact_group", update_fn="update_contact_group",
    )
    cg_member = typer.Typer(help="Contact-group membership.")
    contact_group.add_typer(cg_member, name="member")

    @cg_member.command("add", help="Add contacts to a group (JSON body of Contacts).")
    def _cg_member_add(
        group_id: str = typer.Argument(..., help="Contact group id (GUID)."),
        data: Optional[str] = typer.Option(None, "--data", help="{\"Contacts\":[...]} as JSON (or -f / stdin)."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = read_data(data, file)
        do_write(
            lambda: _client().accounting.add_contact_group_members(group_id, body),
            f"add members to group {group_id}", yes=yes, output_json=output_json,
        )

    @cg_member.command("remove", help="Remove a contact from a group.")
    def _cg_member_remove(
        group_id: str = typer.Argument(..., help="Contact group id (GUID)."),
        contact_id: str = typer.Argument(..., help="Contact id (GUID) to remove."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        do_write(
            lambda: _client().accounting.remove_contact_group_member(group_id, contact_id),
            f"remove {contact_id} from group {group_id}",
            confirm=f"Remove {contact_id} from group {group_id}?",
            yes=yes, output_json=output_json,
        )

    credit_note = _resource(
        app, "credit-note", "credit note",
        [("ID", "CreditNoteID"), ("Number", "CreditNoteNumber"), ("Type", "Type"),
         ("Contact", _contact), ("Total", "Total"), ("Status", "Status")],
        list_fn="list_credit_notes", get_fn="get_credit_note",
        create_fn="create_credit_note", update_fn="update_credit_note",
    )
    _add_allocate(credit_note, "allocate_credit_note", "credit note", "credit note")
    _add_pdf(credit_note, "get_credit_note_pdf", "credit note", "credit note")

    _resource(
        app, "currency", "currency",
        [("Code", "Code"), ("Description", "Description")],
        list_fn="list_currencies", list_filters=False, create_fn="create_currency",
    )

    _resource(
        app, "employee", "employee",
        [("ID", "EmployeeID"), ("First", "FirstName"), ("Last", "LastName"), ("Status", "Status")],
        list_fn="list_employees", get_fn="get_employee",
        create_fn="create_employee", update_fn="update_employee",
    )

    invoice = _resource(
        app, "invoice", "invoice",
        [("ID", "InvoiceID"), ("Number", "InvoiceNumber"), ("Type", "Type"),
         ("Contact", _contact), ("Date", "Date"), ("Due", "DueDate"),
         ("Total", "Total"), ("Status", "Status")],
        list_fn="list_invoices", get_fn="get_invoice",
        create_fn="create_invoice", update_fn="update_invoice",
    )

    @invoice.command("email", help="Email the invoice to the contact (sends real mail).")
    def _invoice_email(
        guid: str = typer.Argument(..., help="Invoice id (GUID)."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        do_write(
            lambda: _client().accounting.email_invoice(guid),
            f"email invoice {guid}",
            confirm=f"Email invoice {guid} to the contact? (real mail)",
            yes=yes, output_json=output_json,
        )

    @invoice.command("online-url", help="Show the invoice's online-invoice URL.")
    def _invoice_online_url(
        guid: str = typer.Argument(..., help="Invoice id (GUID)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            item = _client().accounting.get_invoice_online_url(guid)
        except Exception as e:
            typer.echo(f"Error fetching invoice online URL: {e}", err=True)
            raise typer.Exit(1)
        emit_record(item, output_json)

    _add_pdf(invoice, "get_invoice_pdf", "invoice", "invoice")

    _resource(
        app, "item", "item",
        [("ID", "ItemID"), ("Code", "Code"), ("Name", "Name"), ("Description", "Description")],
        list_fn="list_items", get_fn="get_item",
        create_fn="create_item", update_fn="update_item", delete_fn="delete_item",
    )

    journal = _resource(
        app, "journal", "journal",
        [("ID", "JournalID"), ("Number", "JournalNumber"),
         ("Date", "JournalDate"), ("Reference", "Reference")],
        get_fn="get_journal",
    )

    @journal.command("list", help="List journals (batched via the JournalNumber offset).")
    def _journal_list(
        offset: Optional[int] = typer.Option(None, "--offset", help="Start after this JournalNumber."),
        fetch_all: bool = typer.Option(False, "--all", help="Fetch every batch (default: the first batch only)."),
        limit: Optional[int] = typer.Option(None, "--limit", help="Fetch up to N records across batches (--all wins)."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            items = _client().accounting.list_journals(
                offset=offset, all_pages=fetch_all, limit=None if fetch_all else limit)
        except Exception as e:
            typer.echo(f"Error fetching journals: {e}", err=True)
            raise typer.Exit(1)
        _list_hint(items, fetch_all, limit)
        emit_list(
            items,
            [("ID", "JournalID"), ("Number", "JournalNumber"),
             ("Date", "JournalDate"), ("Reference", "Reference")],
            "journal", output_json,
        )

    _resource(
        app, "linked-transaction", "linked transaction",
        [("ID", "LinkedTransactionID"), ("Source", "SourceTransactionID"),
         ("Target", "TargetTransactionID"), ("Status", "Status")],
        list_fn="list_linked_transactions", get_fn="get_linked_transaction",
        create_fn="create_linked_transaction", update_fn="update_linked_transaction",
        delete_fn="delete_linked_transaction",
    )

    _resource(
        app, "manual-journal", "manual journal",
        [("ID", "ManualJournalID"), ("Narration", "Narration"),
         ("Date", "Date"), ("Status", "Status")],
        list_fn="list_manual_journals", get_fn="get_manual_journal",
        create_fn="create_manual_journal", update_fn="update_manual_journal",
    )

    organisation = typer.Typer(help="Xero organisation (read-only singleton).")
    app.add_typer(organisation, name="organisation")

    @organisation.command("get", help="Show the organisation.")
    def _organisation_get(
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            item = _client().accounting.get_organisation()
        except Exception as e:
            typer.echo(f"Error fetching organisation: {e}", err=True)
            raise typer.Exit(1)
        emit_record(item, output_json)

    overpayment = _resource(
        app, "overpayment", "overpayment",
        [("ID", "OverpaymentID"), ("Type", "Type"), ("Contact", _contact),
         ("Total", "Total"), ("Status", "Status")],
        list_fn="list_overpayments", get_fn="get_overpayment",
    )
    _add_allocate(overpayment, "allocate_overpayment", "overpayment", "overpayment")

    _resource(
        app, "payment", "payment",
        [("ID", "PaymentID"), ("Date", "Date"), ("Amount", "Amount"),
         ("Reference", "Reference"), ("Status", "Status")],
        list_fn="list_payments", get_fn="get_payment",
        create_fn="create_payment", remove=("delete", "delete_payment"),
    )

    _resource(
        app, "payment-service", "payment service",
        [("ID", "PaymentServiceID"), ("Name", "PaymentServiceName"), ("Type", "PaymentServiceType")],
        list_fn="list_payment_services", list_filters=False, create_fn="create_payment_service",
    )

    prepayment = _resource(
        app, "prepayment", "prepayment",
        [("ID", "PrepaymentID"), ("Type", "Type"), ("Contact", _contact),
         ("Total", "Total"), ("Status", "Status")],
        list_fn="list_prepayments", get_fn="get_prepayment",
    )
    _add_allocate(prepayment, "allocate_prepayment", "prepayment", "prepayment")

    purchase_order = _resource(
        app, "purchase-order", "purchase order",
        [("ID", "PurchaseOrderID"), ("Number", "PurchaseOrderNumber"), ("Contact", _contact),
         ("Date", "Date"), ("Total", "Total"), ("Status", "Status")],
        list_fn="list_purchase_orders", get_fn="get_purchase_order",
        create_fn="create_purchase_order", update_fn="update_purchase_order",
    )
    _add_pdf(purchase_order, "get_purchase_order_pdf", "purchase order", "purchase order")

    quote = _resource(
        app, "quote", "quote",
        [("ID", "QuoteID"), ("Number", "QuoteNumber"), ("Contact", _contact),
         ("Date", "Date"), ("Total", "Total"), ("Status", "Status")],
        list_fn="list_quotes", get_fn="get_quote",
        create_fn="create_quote", update_fn="update_quote",
    )
    _add_pdf(quote, "get_quote_pdf", "quote", "quote")

    _resource(
        app, "receipt", "receipt",
        [("ID", "ReceiptID"), ("Number", "ReceiptNumber"),
         ("Date", "Date"), ("Total", "Total"), ("Status", "Status")],
        list_fn="list_receipts", get_fn="get_receipt",
        create_fn="create_receipt", update_fn="update_receipt",
    )

    _resource(
        app, "repeating-invoice", "repeating invoice",
        [("ID", "RepeatingInvoiceID"), ("Reference", "Reference"),
         ("Type", "Type"), ("Total", "Total"), ("Status", "Status")],
        list_fn="list_repeating_invoices", get_fn="get_repeating_invoice",
        create_fn="create_repeating_invoice", update_fn="update_repeating_invoice",
        delete_fn="delete_repeating_invoice",
    )

    _register_reports(app)

    tax_rate = _resource(
        app, "tax-rate", "tax rate",
        [("Name", "Name"), ("Type", "TaxType"), ("Rate", "DisplayTaxRate"), ("Status", "Status")],
        list_fn="list_tax_rates", create_fn="create_tax_rate",
    )

    @tax_rate.command("update", help="Update a tax rate (POSTs the whole TaxRate object; no GUID).")
    def _tax_rate_update(
        data: Optional[str] = typer.Option(None, "--data", help="Full TaxRate object as JSON (or -f / stdin)."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = read_data(data, file)
        do_write(
            lambda: _client().accounting.update_tax_rate(body),
            "update tax rate", confirm="Update this tax rate?",
            yes=yes, output_json=output_json,
        )

    tracking_category = _resource(
        app, "tracking-category", "tracking category",
        [("ID", "TrackingCategoryID"), ("Name", "Name"), ("Status", "Status"),
         ("Options", lambda c: len(c.get("Options") or []))],
        list_fn="list_tracking_categories", get_fn="get_tracking_category",
        create_fn="create_tracking_category", update_fn="update_tracking_category",
        delete_fn="delete_tracking_category",
    )
    tc_option = typer.Typer(help="Tracking-category options.")
    tracking_category.add_typer(tc_option, name="option")

    @tc_option.command("add", help="Add an option to a tracking category (JSON body).")
    def _tc_option_add(
        category_id: str = typer.Argument(..., help="Tracking category id (GUID)."),
        data: Optional[str] = typer.Option(None, "--data", help="Option(s) as JSON (or -f / stdin)."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = read_data(data, file)
        do_write(
            lambda: _client().accounting.add_tracking_option(category_id, body),
            f"add option to category {category_id}", yes=yes, output_json=output_json,
        )

    @tc_option.command("update", help="Update a tracking-category option (JSON body).")
    def _tc_option_update(
        category_id: str = typer.Argument(..., help="Tracking category id (GUID)."),
        option_id: str = typer.Argument(..., help="Option id (GUID)."),
        data: Optional[str] = typer.Option(None, "--data", help="Option object as JSON (or -f / stdin)."),
        file: Optional[str] = typer.Option(None, "-f", "--file", help="Read the JSON body from a file."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        body = read_data(data, file)
        do_write(
            lambda: _client().accounting.update_tracking_option(category_id, option_id, body),
            f"update option {option_id}", confirm=f"Update option {option_id}?",
            yes=yes, output_json=output_json,
        )

    @tc_option.command("delete", help="Delete a tracking-category option.")
    def _tc_option_delete(
        category_id: str = typer.Argument(..., help="Tracking category id (GUID)."),
        option_id: str = typer.Argument(..., help="Option id (GUID)."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON of the result."),
    ):
        do_write(
            lambda: _client().accounting.delete_tracking_option(category_id, option_id),
            f"delete option {option_id}", confirm=f"Delete option {option_id}?",
            yes=yes, output_json=output_json,
        )

    _resource(
        app, "user", "user",
        [("ID", "UserID"), ("Email", "EmailAddress"), ("First", "FirstName"),
         ("Last", "LastName"), ("Role", "OrganisationRole")],
        list_fn="list_users", get_fn="get_user",
    )


def _register_reports(app: typer.Typer) -> None:
    """Attach the `report` sub-app: `list` plus one subcommand per named report."""
    report = typer.Typer(help="Xero reports (read-only).")
    app.add_typer(report, name="report")

    @report.command("list", help="List the available reports.")
    def _report_list(
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        try:
            items = _client().accounting.list_reports()
        except Exception as e:
            typer.echo(f"Error fetching reports: {e}", err=True)
            raise typer.Exit(1)
        emit_list(
            items,
            [("ID", "ReportID"), ("Name", "ReportName"), ("Type", "ReportType")],
            "report", output_json,
        )

    for cmd_name, xero_name in REPORT_NAMES.items():
        _add_report(report, cmd_name, xero_name)


def _add_report(report: typer.Typer, cmd_name: str, xero_name: str) -> None:
    """Add a named-report subcommand calling get_report with the common params."""

    @report.command(cmd_name, help=f"{xero_name} report.")
    def _report(
        date: Optional[str] = typer.Option(None, "--date", help="Report date (YYYY-MM-DD)."),
        from_date: Optional[str] = typer.Option(None, "--from-date", help="Period start (fromDate)."),
        to_date: Optional[str] = typer.Option(None, "--to-date", help="Period end (toDate)."),
        param: Optional[List[str]] = typer.Option(
            None, "--param", help="Extra report param as KEY=VALUE (repeatable)."
        ),
        output_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
    ):
        params = {}
        if date:
            params["date"] = date
        if from_date:
            params["fromDate"] = from_date
        if to_date:
            params["toDate"] = to_date
        for kv in param or []:
            if "=" not in kv:
                typer.echo(f"Error: --param must be KEY=VALUE, got {kv!r}.", err=True)
                raise typer.Exit(1)
            k, v = kv.split("=", 1)
            params[k] = v
        try:
            result = _client().accounting.get_report(xero_name, params or None)
        except Exception as e:
            typer.echo(f"Error fetching {cmd_name} report: {e}", err=True)
            raise typer.Exit(1)
        emit_record(result, output_json)
