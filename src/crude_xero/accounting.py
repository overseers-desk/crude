"""Xero Accounting API (api.xro/2.0) method groups over a XeroSession.

One method group per Accounting resource: list_* auto-pages via the session's
`page` loop, get_* unwraps the single-element envelope, create uses PUT and
update uses POST (Xero's create/update convention), and the irregular verbs
(allocate, pdf, email, online-url, status-deletes) sit beside their resource.
Two cross-cutting groups — attachments and history — are parameterised by a
friendly parent name resolved against a whitelist, not duplicated per resource.
"""

from __future__ import annotations

from datetime import timezone

from crude_common import asof
from crude_xero.client import XeroError, _extract_list

BASE = "accounting"

# WORLD_AS_OF boundary tables. Xero exposes no created-date filter on most
# accounting collections, so the bound rides UpdatedDateUTC and OVER-EXCLUDES:
# a pre-cutoff invoice edited yesterday disappears rather than being served
# newer than it claims. That is the conservative choice: absence is honest, a
# silently newer body is not.
#
# _ASOF_SERVER_BOUND: collections documented to carry UpdatedDateUTC and take a
# ``where`` filter get the clause injected server-side (the client-side drop
# below still runs as the belt). The others are bounded client-side only.
_ASOF_SERVER_BOUND = {
    "Accounts", "BankTransactions", "BatchPayments", "Contacts", "CreditNotes",
    "Employees", "Invoices", "Items", "LinkedTransactions", "ManualJournals",
    "Overpayments", "Payments", "Prepayments", "PurchaseOrders", "Quotes",
    "Receipts", "Users",
}

# _ASOF_STAMP: the exclusion stamp per collection where it is not the default
# UpdatedDateUTC. None = stamp-less: nothing can be dropped or dated, so the
# rows are served current-state-flagged. BankTransfers are immutable after
# create, so their CreatedDateUTC drop is exact, the append-only ideal.
_ASOF_STAMP = {
    "BankTransfers": "CreatedDateUTC",
    "BrandingThemes": "CreatedDateUTC",
    "Currencies": None,
    "PaymentServices": None,
    "ContactGroups": None,
    "TaxRates": None,
    "TrackingCategories": None,
    "RepeatingInvoices": None,
}


def _asof_stamp(collection):
    return _ASOF_STAMP.get(collection, "UpdatedDateUTC")


def asof_clamp_report_params(params, *, inject_date=True):
    """Clamp a report's date params to the WORLD_AS_OF cutoff's date.

    ``date``/``toDate``/``balanceDate`` are clamped (an unparseable value is
    replaced, never trusted); a ``fromDate`` after the cutoff refuses; with no
    period at all, ``date`` is injected so the endpoint does not default to
    today. Shared by the Accounting reports and the Finance statements. Any
    report is still computed from today's ledger over the period (a post-cutoff
    back-dated edit leaks in), so the disclosure is emitted here.
    """
    b = asof.world_as_of()
    if b is None:
        return params
    out = dict(params or {})
    asof.check_window_start(out.get("fromDate"), "fromDate")
    cap = b.date().isoformat()
    for key in ("date", "toDate", "balanceDate"):
        if key in out:
            v = asof.parse_stamp(out[key])
            if v is None or v > b:
                out[key] = cap
    if inject_date and not any(k in out for k in ("date", "fromDate", "toDate", "balanceDate")):
        out["date"] = cap
    asof.emit_current_state(
        "this report (computed from today's ledger over the bounded period)")
    return out


def _asof_where(collection, where):
    """The caller's where with ``UpdatedDateUTC <= bound`` AND-composed in."""
    b = asof.world_as_of()
    if b is None or collection not in _ASOF_SERVER_BOUND:
        return where
    u = b.astimezone(timezone.utc)
    clause = (f"UpdatedDateUTC <= DateTime({u.year},{u.month},{u.day},"
              f"{u.hour},{u.minute},{u.second})")
    return f"({where}) AND {clause}" if where else clause

# Friendly singular -> Xero collection, for the attachment-capable resources.
ATTACHMENT_ENDPOINTS = {
    "account": "Accounts",
    "bank-transaction": "BankTransactions",
    "bank-transfer": "BankTransfers",
    "batch-payment": "BatchPayments",
    "contact": "Contacts",
    "credit-note": "CreditNotes",
    "invoice": "Invoices",
    "manual-journal": "ManualJournals",
    "overpayment": "Overpayments",
    "prepayment": "Prepayments",
    "purchase-order": "PurchaseOrders",
    "quote": "Quotes",
    "receipt": "Receipts",
    "repeating-invoice": "RepeatingInvoices",
}

# Friendly singular -> Xero collection, for the history-capable resources.
HISTORY_ENDPOINTS = {
    "bank-transaction": "BankTransactions",
    "contact": "Contacts",
    "credit-note": "CreditNotes",
    "invoice": "Invoices",
    "manual-journal": "ManualJournals",
    "overpayment": "Overpayments",
    "payment": "Payments",
    "prepayment": "Prepayments",
    "purchase-order": "PurchaseOrders",
    "quote": "Quotes",
    "receipt": "Receipts",
    "repeating-invoice": "RepeatingInvoices",
}

# Friendly name -> Xero report endpoint name, for `report get <name>`.
# (The AU BAS/GST report name is unverified; see docs at implementation time.)
REPORT_NAMES = {
    "balance-sheet": "BalanceSheet",
    "profit-and-loss": "ProfitAndLoss",
    "trial-balance": "TrialBalance",
    "aged-receivables": "AgedReceivablesByContact",
    "aged-payables": "AgedPayablesByContact",
    "bank-summary": "BankSummary",
    "bas": "BASReport",
    "gst": "BASReport",
    "executive-summary": "ExecutiveSummary",
    "budget-summary": "BudgetSummary",
}


class AccountingAPI:
    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _list(self, collection, *, where=None, order=None, all_pages=False, limit=None, **params):
        """List a collection (first page by default; all_pages/limit widen it), dropping unset filters.

        Under WORLD_AS_OF the bound is enforced here for every accounting list:
        the ``UpdatedDateUTC <=`` where clause server-side (whitelisted
        collections), the client-side stamp drop as the belt, and the
        current-state flag for the stamp-less collections.
        """
        query = {"where": _asof_where(collection, where), "order": order}
        query.update(params)
        query = {k: v for k, v in query.items() if v is not None}
        items = self.session.paginate(BASE, collection, params=query or None,
                                      all_pages=all_pages, limit=limit)
        stamp = _asof_stamp(collection)
        if stamp is None:
            return asof.current_state(items, collection)
        return asof.bound_records(items, stamp, what=collection)

    def _one(self, data):
        """Unwrap a single record from the list-wrapped get response."""
        items = _extract_list(data)
        if items:
            return items[0]
        return data if isinstance(data, dict) else {}

    def _get_one(self, collection, guid):
        record = self._one(self.session._get(BASE, f"{collection}/{guid}"))
        # The same conservative rule as the lists: a record touched after the
        # cutoff is excluded, not served newer than it claims. Stamp-less
        # collections are served current-state-flagged.
        stamp = _asof_stamp(collection)
        if stamp is None:
            return asof.current_state(record, f"this {collection} record")
        return asof.deny_newer(record, stamp, f"{collection} record")

    def _create(self, collection, body):
        return self.session._put(BASE, collection, json=body)

    def _update(self, collection, guid, body):
        return self.session._post(BASE, f"{collection}/{guid}", json=body)

    def _delete(self, collection, guid):
        return self.session._delete(BASE, f"{collection}/{guid}")

    def _set_status(self, collection, guid, status):
        """Soft-delete/archive by POSTing a status change (Xero has no DELETE verb here)."""
        return self.session._post(BASE, f"{collection}/{guid}", json={"Status": status})

    def _pdf(self, collection, guid):
        # A PDF renders the record's live state and its bytes carry no stamp to
        # flag, so under WORLD_AS_OF the render is gated on the record's
        # metadata: the JSON get applies the same conservative rule (deny_newer,
        # or current-state for a stamp-less collection), refusing the download
        # for a record touched after the cutoff before any bytes leave the tool.
        if asof.active():
            self._get_one(collection, guid)
        return self.session._get(BASE, f"{collection}/{guid}", accept="application/pdf")

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    def list_accounts(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("Accounts", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_account(self, guid):
        return self._get_one("Accounts", guid)

    def create_account(self, body):
        return self._create("Accounts", body)

    def update_account(self, guid, body):
        return self._update("Accounts", guid, body)

    def delete_account(self, guid):
        return self._delete("Accounts", guid)

    # ------------------------------------------------------------------
    # Bank transactions (no hard delete; soft-delete via update Status=DELETED)
    # ------------------------------------------------------------------

    def list_bank_transactions(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("BankTransactions", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_bank_transaction(self, guid):
        return self._get_one("BankTransactions", guid)

    def create_bank_transaction(self, body):
        return self._create("BankTransactions", body)

    def update_bank_transaction(self, guid, body):
        return self._update("BankTransactions", guid, body)

    # ------------------------------------------------------------------
    # Bank transfers (immutable after create)
    # ------------------------------------------------------------------

    def list_bank_transfers(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("BankTransfers", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_bank_transfer(self, guid):
        return self._get_one("BankTransfers", guid)

    def create_bank_transfer(self, body):
        return self._create("BankTransfers", body)

    # ------------------------------------------------------------------
    # Batch payments (delete via Status=DELETED)
    # ------------------------------------------------------------------

    def list_batch_payments(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("BatchPayments", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_batch_payment(self, guid):
        return self._get_one("BatchPayments", guid)

    def create_batch_payment(self, body):
        return self._create("BatchPayments", body)

    def delete_batch_payment(self, guid):
        return self._set_status("BatchPayments", guid, "DELETED")

    # ------------------------------------------------------------------
    # Branding themes (read-only)
    # ------------------------------------------------------------------

    def list_branding_themes(self, all_pages=False, limit=None):
        return self._list("BrandingThemes", all_pages=all_pages, limit=limit)

    def get_branding_theme(self, guid):
        return self._get_one("BrandingThemes", guid)

    # ------------------------------------------------------------------
    # Budgets (read-only)
    # ------------------------------------------------------------------

    def list_budgets(self, all_pages=False, limit=None):
        return self._list("Budgets", all_pages=all_pages, limit=limit)

    def get_budget(self, guid):
        return self._get_one("Budgets", guid)

    # ------------------------------------------------------------------
    # Contacts (archive via Status=ARCHIVED; no hard delete)
    # ------------------------------------------------------------------

    def list_contacts(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("Contacts", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_contact(self, guid):
        return self._get_one("Contacts", guid)

    def create_contact(self, body):
        return self._create("Contacts", body)

    def update_contact(self, guid, body):
        return self._update("Contacts", guid, body)

    def archive_contact(self, guid):
        return self._set_status("Contacts", guid, "ARCHIVED")

    # ------------------------------------------------------------------
    # Contact groups (and their membership)
    # ------------------------------------------------------------------

    def list_contact_groups(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("ContactGroups", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_contact_group(self, guid):
        return self._get_one("ContactGroups", guid)

    def create_contact_group(self, body):
        return self._create("ContactGroups", body)

    def update_contact_group(self, guid, body):
        return self._update("ContactGroups", guid, body)

    def add_contact_group_members(self, guid, body):
        return self.session._put(BASE, f"ContactGroups/{guid}/Contacts", json=body)

    def remove_contact_group_member(self, guid, contact_id):
        return self.session._delete(BASE, f"ContactGroups/{guid}/Contacts/{contact_id}")

    # ------------------------------------------------------------------
    # Credit notes (allocate to an invoice; PDF)
    # ------------------------------------------------------------------

    def list_credit_notes(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("CreditNotes", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_credit_note(self, guid):
        return self._get_one("CreditNotes", guid)

    def create_credit_note(self, body):
        return self._create("CreditNotes", body)

    def update_credit_note(self, guid, body):
        return self._update("CreditNotes", guid, body)

    def allocate_credit_note(self, guid, body):
        return self.session._put(BASE, f"CreditNotes/{guid}/Allocations", json=body)

    def get_credit_note_pdf(self, guid):
        return self._pdf("CreditNotes", guid)

    # ------------------------------------------------------------------
    # Currencies
    # ------------------------------------------------------------------

    def list_currencies(self, all_pages=False, limit=None):
        return self._list("Currencies", all_pages=all_pages, limit=limit)

    def create_currency(self, body):
        return self._create("Currencies", body)

    # ------------------------------------------------------------------
    # Employees (accounting-side, not Payroll)
    # ------------------------------------------------------------------

    def list_employees(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("Employees", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_employee(self, guid):
        return self._get_one("Employees", guid)

    def create_employee(self, body):
        return self._create("Employees", body)

    def update_employee(self, guid, body):
        return self._update("Employees", guid, body)

    # ------------------------------------------------------------------
    # Invoices (email, online URL, PDF)
    # ------------------------------------------------------------------

    def list_invoices(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("Invoices", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_invoice(self, guid):
        return self._get_one("Invoices", guid)

    def create_invoice(self, body):
        return self._create("Invoices", body)

    def update_invoice(self, guid, body):
        return self._update("Invoices", guid, body)

    def email_invoice(self, guid):
        """Send the invoice to the contact's email (real mail)."""
        return self.session._post(BASE, f"Invoices/{guid}/Email", json={})

    def get_invoice_online_url(self, guid):
        return self.session._get(BASE, f"Invoices/{guid}/OnlineInvoice")

    def get_invoice_pdf(self, guid):
        return self._pdf("Invoices", guid)

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    def list_items(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("Items", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_item(self, guid):
        return self._get_one("Items", guid)

    def create_item(self, body):
        return self._create("Items", body)

    def update_item(self, guid, body):
        return self._update("Items", guid, body)

    def delete_item(self, guid):
        return self._delete("Items", guid)

    # ------------------------------------------------------------------
    # Journals (read-only; paged by offset = last JournalNumber, not page)
    # ------------------------------------------------------------------

    def list_journals(self, offset=None, all_pages=False, limit=None):
        """Page journals via the trailing JournalNumber offset (not the page param).

        First batch only by default; all_pages walks every batch to the end, and
        limit caps the total records (paging as needed, then truncating).
        """
        from crude_xero.client import PAGE_SIZE
        results = []
        cursor = offset
        while True:
            params = {"offset": cursor} if cursor is not None else None
            chunk = _extract_list(self.session._get(BASE, "Journals", params=params))
            results.extend(chunk)
            if limit is not None and len(results) >= limit:
                break
            if len(chunk) < PAGE_SIZE:
                break
            cursor = chunk[-1].get("JournalNumber")
            if cursor is None:
                break
            if limit is None and not all_pages:
                break
        results = results[:limit] if limit is not None else results
        # Journals are append-only with a CreatedDateUTC: the one exact as-of
        # surface in the whole product. Post-filter, nothing to flag.
        return asof.bound_records(results, "CreatedDateUTC", what="journal")

    def get_journal(self, guid):
        return self._get_one("Journals", guid)

    # ------------------------------------------------------------------
    # Linked transactions
    # ------------------------------------------------------------------

    def list_linked_transactions(self, all_pages=False, limit=None, **params):
        return self._list("LinkedTransactions", all_pages=all_pages, limit=limit, **params)

    def get_linked_transaction(self, guid):
        return self._get_one("LinkedTransactions", guid)

    def create_linked_transaction(self, body):
        return self._create("LinkedTransactions", body)

    def update_linked_transaction(self, guid, body):
        return self._update("LinkedTransactions", guid, body)

    def delete_linked_transaction(self, guid):
        return self._delete("LinkedTransactions", guid)

    # ------------------------------------------------------------------
    # Manual journals
    # ------------------------------------------------------------------

    def list_manual_journals(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("ManualJournals", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_manual_journal(self, guid):
        return self._get_one("ManualJournals", guid)

    def create_manual_journal(self, body):
        return self._create("ManualJournals", body)

    def update_manual_journal(self, guid, body):
        return self._update("ManualJournals", guid, body)

    # ------------------------------------------------------------------
    # Organisation (read-only singleton)
    # ------------------------------------------------------------------

    def get_organisation(self):
        return self._one(self.session._get(BASE, "Organisation"))

    # ------------------------------------------------------------------
    # Overpayments (allocate to an invoice)
    # ------------------------------------------------------------------

    def list_overpayments(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("Overpayments", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_overpayment(self, guid):
        return self._get_one("Overpayments", guid)

    def allocate_overpayment(self, guid, body):
        return self.session._put(BASE, f"Overpayments/{guid}/Allocations", json=body)

    # ------------------------------------------------------------------
    # Payments (delete via Status=DELETED)
    # ------------------------------------------------------------------

    def list_payments(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("Payments", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_payment(self, guid):
        return self._get_one("Payments", guid)

    def create_payment(self, body):
        return self._create("Payments", body)

    def delete_payment(self, guid):
        return self._set_status("Payments", guid, "DELETED")

    # ------------------------------------------------------------------
    # Payment services
    # ------------------------------------------------------------------

    def list_payment_services(self, all_pages=False, limit=None):
        return self._list("PaymentServices", all_pages=all_pages, limit=limit)

    def create_payment_service(self, body):
        return self._create("PaymentServices", body)

    # ------------------------------------------------------------------
    # Prepayments (allocate to an invoice)
    # ------------------------------------------------------------------

    def list_prepayments(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("Prepayments", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_prepayment(self, guid):
        return self._get_one("Prepayments", guid)

    def allocate_prepayment(self, guid, body):
        return self.session._put(BASE, f"Prepayments/{guid}/Allocations", json=body)

    # ------------------------------------------------------------------
    # Purchase orders (PDF)
    # ------------------------------------------------------------------

    def list_purchase_orders(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("PurchaseOrders", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_purchase_order(self, guid):
        return self._get_one("PurchaseOrders", guid)

    def create_purchase_order(self, body):
        return self._create("PurchaseOrders", body)

    def update_purchase_order(self, guid, body):
        return self._update("PurchaseOrders", guid, body)

    def get_purchase_order_pdf(self, guid):
        return self._pdf("PurchaseOrders", guid)

    # ------------------------------------------------------------------
    # Quotes (PDF)
    # ------------------------------------------------------------------

    def list_quotes(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("Quotes", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_quote(self, guid):
        return self._get_one("Quotes", guid)

    def create_quote(self, body):
        return self._create("Quotes", body)

    def update_quote(self, guid, body):
        return self._update("Quotes", guid, body)

    def get_quote_pdf(self, guid):
        return self._pdf("Quotes", guid)

    # ------------------------------------------------------------------
    # Receipts
    # ------------------------------------------------------------------

    def list_receipts(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("Receipts", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_receipt(self, guid):
        return self._get_one("Receipts", guid)

    def create_receipt(self, body):
        return self._create("Receipts", body)

    def update_receipt(self, guid, body):
        return self._update("Receipts", guid, body)

    # ------------------------------------------------------------------
    # Repeating invoices
    # ------------------------------------------------------------------

    def list_repeating_invoices(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("RepeatingInvoices", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_repeating_invoice(self, guid):
        return self._get_one("RepeatingInvoices", guid)

    def create_repeating_invoice(self, body):
        return self._create("RepeatingInvoices", body)

    def update_repeating_invoice(self, guid, body):
        return self._update("RepeatingInvoices", guid, body)

    def delete_repeating_invoice(self, guid):
        return self._delete("RepeatingInvoices", guid)

    # ------------------------------------------------------------------
    # Reports (read-only)
    # ------------------------------------------------------------------

    def list_reports(self):
        return _extract_list(self.session._get(BASE, "Reports"))

    def get_report(self, report_name, params=None):
        """Fetch a named report (report_name is the Xero name, e.g. BalanceSheet).

        Under WORLD_AS_OF the date params are clamped to the cutoff's date (and
        ``date`` injected when the caller set no period at all, since the
        endpoint would otherwise default to today). The report is still
        computed from today's ledger over that period (a post-cutoff back-dated
        edit leaks in), so it is disclosed as computed-now.
        """
        params = asof_clamp_report_params(params)
        return self.session._get(BASE, f"Reports/{report_name}", params=params)

    # ------------------------------------------------------------------
    # Tax rates (no GUID; update/soft-delete by POSTing the whole object)
    # ------------------------------------------------------------------

    def list_tax_rates(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("TaxRates", where=where, order=order, all_pages=all_pages, limit=limit)

    def create_tax_rate(self, body):
        return self._create("TaxRates", body)

    def update_tax_rate(self, body):
        return self.session._post(BASE, "TaxRates", json=body)

    # ------------------------------------------------------------------
    # Tracking categories (and their options)
    # ------------------------------------------------------------------

    def list_tracking_categories(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("TrackingCategories", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_tracking_category(self, guid):
        return self._get_one("TrackingCategories", guid)

    def create_tracking_category(self, body):
        return self._create("TrackingCategories", body)

    def update_tracking_category(self, guid, body):
        return self._update("TrackingCategories", guid, body)

    def delete_tracking_category(self, guid):
        return self._delete("TrackingCategories", guid)

    def add_tracking_option(self, category_guid, body):
        return self.session._put(BASE, f"TrackingCategories/{category_guid}/Options", json=body)

    def update_tracking_option(self, category_guid, option_guid, body):
        return self.session._post(
            BASE, f"TrackingCategories/{category_guid}/Options/{option_guid}", json=body)

    def delete_tracking_option(self, category_guid, option_guid):
        return self.session._delete(
            BASE, f"TrackingCategories/{category_guid}/Options/{option_guid}")

    # ------------------------------------------------------------------
    # Users (read-only)
    # ------------------------------------------------------------------

    def list_users(self, where=None, order=None, all_pages=False, limit=None):
        return self._list("Users", where=where, order=order, all_pages=all_pages, limit=limit)

    def get_user(self, guid):
        return self._get_one("Users", guid)

    # ------------------------------------------------------------------
    # Cross-cutting: attachments (parameterised by parent type)
    # ------------------------------------------------------------------

    def _attachment_collection(self, endpoint):
        try:
            return ATTACHMENT_ENDPOINTS[endpoint]
        except KeyError:
            valid = ", ".join(sorted(ATTACHMENT_ENDPOINTS))
            raise XeroError(f"'{endpoint}' takes no attachments; valid: {valid}.")

    def list_attachments(self, endpoint, guid):
        collection = self._attachment_collection(endpoint)
        return _extract_list(self.session._get(BASE, f"{collection}/{guid}/Attachments"))

    def get_attachment(self, endpoint, guid, file_id_or_name):
        """Download an attachment's bytes by file id or filename."""
        collection = self._attachment_collection(endpoint)
        # Same byte-path rule as _pdf: gate the download on the parent record's
        # stamp. Adding an attachment bumps the parent's UpdatedDateUTC, so a
        # record touched after the cutoff is excluded before its bytes leave.
        if asof.active():
            self._get_one(collection, guid)
        return self.session._get(
            BASE, f"{collection}/{guid}/Attachments/{file_id_or_name}",
            accept="application/octet-stream")

    def add_attachment(self, endpoint, guid, filename, content: bytes, mime):
        """Upload an attachment (raw bytes) under a filename."""
        collection = self._attachment_collection(endpoint)
        return self.session._put_raw(
            BASE, f"{collection}/{guid}/Attachments/{filename}",
            data=content, content_type=mime)

    # ------------------------------------------------------------------
    # Cross-cutting: history & notes (parameterised by parent type)
    # ------------------------------------------------------------------

    def _history_collection(self, endpoint):
        try:
            return HISTORY_ENDPOINTS[endpoint]
        except KeyError:
            valid = ", ".join(sorted(HISTORY_ENDPOINTS))
            raise XeroError(f"'{endpoint}' has no history; valid: {valid}.")

    def list_history(self, endpoint, guid):
        collection = self._history_collection(endpoint)
        return _extract_list(self.session._get(BASE, f"{collection}/{guid}/History"))

    def add_history(self, endpoint, guid, note):
        collection = self._history_collection(endpoint)
        body = {"HistoryRecords": [{"Details": note}]}
        return self.session._put(BASE, f"{collection}/{guid}/History", json=body)
