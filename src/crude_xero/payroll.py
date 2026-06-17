"""Xero Payroll API (payroll.xro/2.0) method group over a XeroSession.

This is the unified, camelCase Payroll platform shared with Payroll NZ/UK, not
the classic PascalCase Payroll AU product. Every response is wrapped in a
metadata envelope and the payload sits under a camelCase plural key:

    {"id":..., "providerName":..., "dateTimeUTC":..., "httpStatusCode":"OK",
     "pagination":{"page":1,"pageSize":100,"pageCount":2,"itemCount":115},
     "problem":null, "employees":[...]}

A single record comes back the same way under the camelCase *singular* key
(``{"employee":{...}}``), and dates are ISO strings (``"2021-10-25T00:00:00"``),
not Accounting's ``/Date()/``. Because the unwrap helpers key off value *type* —
the lone list for a collection, the lone non-``pagination`` dict for a detail —
they are casing-agnostic and need no per-field knowledge; ``paginate`` walks the
``page`` query param as for Accounting.

Payroll departs from Accounting in its verbs: there is no PUT — a create POSTs to
the collection and an update POSTs to the element (``POST Employees/{id}``), so
this group's ``_create``/``_update`` both POST.

Pay runs are list-only: the single-pay-run detail (``GET PayRuns/{id}``, 405) and
the element update (404) are not served, and with them go the payslip lines — so
payslip-level "who was paid" data is not reachable through this API. Earnings
rates and reimbursements are the platform's split of what the classic API grouped
under PayItems. Settings is a read-only singleton wrapping the linked accounts.
"""

from __future__ import annotations

from crude_xero.client import _extract_list

BASE = "payroll_au"


class PayrollAU:
    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _list(self, collection, *, all_pages=False, limit=None):
        """Page a Payroll collection via the `page` query param.

        First page only by default; `all_pages` walks to the end and `limit` caps
        the total records (paging as needed, then truncating).
        """
        return self.session.paginate(BASE, collection, all_pages=all_pages, limit=limit)

    def _one(self, data):
        """Unwrap a single record from a Payroll get response.

        A detail wraps the record under its camelCase singular key alongside the
        envelope metadata (``{"employee":{...}, "pagination":{...}, ...}``); pull
        the lone non-``pagination`` dict value. A collection-shaped body (record
        under a one-element list) is handled first by `_extract_list`. Falls back
        to the whole dict for any other shape.
        """
        if not isinstance(data, dict):
            return {}
        items = _extract_list(data)
        if items:
            return items[0]
        objs = [v for k, v in data.items() if isinstance(v, dict) and k != "pagination"]
        if len(objs) == 1:
            return objs[0]
        return data

    def _get_one(self, collection, guid):
        return self._one(self.session._get(BASE, f"{collection}/{guid}"))

    def _create(self, collection, body):
        """Create via POST to the collection (Payroll has no PUT)."""
        return self.session._post(BASE, collection, json=body)

    def _update(self, collection, guid, body):
        """Update via POST to the element (Payroll's soft-update convention)."""
        return self.session._post(BASE, f"{collection}/{guid}", json=body)

    def _delete(self, collection, guid):
        return self.session._delete(BASE, f"{collection}/{guid}")

    # ------------------------------------------------------------------
    # Employees
    # ------------------------------------------------------------------

    def list_employees(self, all_pages=False, limit=None):
        return self._list("Employees", all_pages=all_pages, limit=limit)

    def get_employee(self, guid):
        return self._get_one("Employees", guid)

    def create_employee(self, body):
        return self._create("Employees", body)

    def update_employee(self, guid, body):
        return self._update("Employees", guid, body)

    # ------------------------------------------------------------------
    # Pay runs (list-only: the detail/update element routes are not served)
    # ------------------------------------------------------------------

    def list_pay_runs(self, all_pages=False, limit=None):
        return self._list("PayRuns", all_pages=all_pages, limit=limit)

    def create_pay_run(self, body):
        return self._create("PayRuns", body)

    # ------------------------------------------------------------------
    # Pay run calendars
    # ------------------------------------------------------------------

    def list_pay_run_calendars(self, all_pages=False, limit=None):
        return self._list("PayRunCalendars", all_pages=all_pages, limit=limit)

    def get_pay_run_calendar(self, guid):
        return self._get_one("PayRunCalendars", guid)

    def create_pay_run_calendar(self, body):
        return self._create("PayRunCalendars", body)

    # ------------------------------------------------------------------
    # Earnings rates
    # ------------------------------------------------------------------

    def list_earnings_rates(self, all_pages=False, limit=None):
        return self._list("EarningsRates", all_pages=all_pages, limit=limit)

    def get_earnings_rate(self, guid):
        return self._get_one("EarningsRates", guid)

    def create_earnings_rate(self, body):
        return self._create("EarningsRates", body)

    def update_earnings_rate(self, guid, body):
        return self._update("EarningsRates", guid, body)

    # ------------------------------------------------------------------
    # Reimbursements
    # ------------------------------------------------------------------

    def list_reimbursements(self, all_pages=False, limit=None):
        return self._list("Reimbursements", all_pages=all_pages, limit=limit)

    def get_reimbursement(self, guid):
        return self._get_one("Reimbursements", guid)

    def create_reimbursement(self, body):
        return self._create("Reimbursements", body)

    def update_reimbursement(self, guid, body):
        return self._update("Reimbursements", guid, body)

    # ------------------------------------------------------------------
    # Timesheets (full CRUD, including a hard delete)
    # ------------------------------------------------------------------

    def list_timesheets(self, all_pages=False, limit=None):
        return self._list("Timesheets", all_pages=all_pages, limit=limit)

    def get_timesheet(self, guid):
        return self._get_one("Timesheets", guid)

    def create_timesheet(self, body):
        return self._create("Timesheets", body)

    def update_timesheet(self, guid, body):
        return self._update("Timesheets", guid, body)

    def delete_timesheet(self, guid):
        return self._delete("Timesheets", guid)

    # ------------------------------------------------------------------
    # Settings (read-only singleton; singular-object envelope)
    # ------------------------------------------------------------------

    def get_settings(self):
        return self._one(self.session._get(BASE, "Settings"))
