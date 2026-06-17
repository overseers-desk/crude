"""Xero Payroll AU API (payroll.xro/1.0) method group over a XeroSession.

One method group for the Australian Payroll product: employees, pay runs (whose
detail carries the run's payslips), pay items, timesheets, leave applications,
super funds, payroll calendars, and the read-only payslip and settings. Payroll
AU departs from Accounting in two ways the rest of the file leans on:

* Verbs. Payroll AU has no PUT — a create is a POST to the collection and an
  update is a POST to the element (``POST Employees/{id}``), so this group's
  ``_create``/``_update`` both POST, unlike Accounting's PUT-create/POST-update.
* Envelope. A collection comes back under its plural resource key beside the
  ``{Id, Status, ProviderName, DateTimeUTC}`` envelope scalars
  (``{"Employees":[...], "Status":"OK", ...}``). The scalars are not lists, so the
  single-list-key heuristic in ``_extract_list`` resolves to the plural key,
  reused here for the list/get unwrap; `paginate` walks the `page` query param.

Two shapes break that envelope and get handled on their own: ``PayItems`` comes
back as a single grouped object keyed by category (``EarningsRates``,
``DeductionTypes``, ``LeaveTypes``, ``ReimbursementTypes``), each a list, not a
flat paged collection; and a ``Payslip`` or ``Settings`` get wraps a single
object under its key rather than a one-element list — so `_one` falls back to the
lone dict value beside the envelope scalars for those.
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
        """Page a Payroll AU collection via the `page` query param.

        First page only by default; `all_pages` walks to the end and `limit` caps
        the total records (paging as needed, then truncating).
        """
        return self.session.paginate(BASE, collection, all_pages=all_pages, limit=limit)

    def _one(self, data):
        """Unwrap a single record from a Payroll AU get response.

        Most resources wrap the record as a one-element list under the plural key
        (``{"Employees":[{...}]}``) — pull the first element. Payslip and Settings
        instead wrap a single object under their key (``{"Payslip":{...}}``) —
        fall back to the lone dict value beside the envelope scalars. Falls back
        to the whole dict for any other shape.
        """
        if not isinstance(data, dict):
            return {}
        items = _extract_list(data)
        if items:
            return items[0]
        objs = [v for v in data.values() if isinstance(v, dict)]
        if len(objs) == 1:
            return objs[0]
        return data

    def _get_one(self, collection, guid):
        return self._one(self.session._get(BASE, f"{collection}/{guid}"))

    def _create(self, collection, body):
        """Create via POST to the collection (Payroll AU has no PUT)."""
        return self.session._post(BASE, collection, json=body)

    def _update(self, collection, guid, body):
        """Update via POST to the element (Payroll AU's soft-update convention)."""
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
    # Pay runs
    # ------------------------------------------------------------------

    def list_pay_runs(self, all_pages=False, limit=None):
        return self._list("PayRuns", all_pages=all_pages, limit=limit)

    def get_pay_run(self, guid):
        return self._get_one("PayRuns", guid)

    def create_pay_run(self, body):
        return self._create("PayRuns", body)

    def update_pay_run(self, guid, body):
        return self._update("PayRuns", guid, body)

    # ------------------------------------------------------------------
    # Pay items (grouped object, not a flat list; create/update both POST PayItems)
    # ------------------------------------------------------------------

    def list_pay_items(self):
        """Return the PayItems group, keyed by category, each category a list.

        Unlike the other Payroll AU collections, PayItems comes back as a single
        grouped object (``{"PayItems": {"EarningsRates": [...], ...}}``), so it is
        returned whole rather than run through the list unwrap. Falls back to the
        raw body if the ``PayItems`` key is absent.
        """
        data = self.session._get(BASE, "PayItems")
        if isinstance(data, dict):
            return data.get("PayItems", data)
        return data

    def create_pay_item(self, body):
        """Create a pay item (POST PayItems; the body carries the category array)."""
        return self._create("PayItems", body)

    def update_pay_item(self, body):
        """Update a pay item (POST PayItems; there is no element path, so the whole
        category array is POSTed back, mirroring Accounting's GUID-less tax-rate update)."""
        return self._create("PayItems", body)

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
    # Leave applications
    # ------------------------------------------------------------------

    def list_leave_applications(self, all_pages=False, limit=None):
        return self._list("LeaveApplications", all_pages=all_pages, limit=limit)

    def get_leave_application(self, guid):
        return self._get_one("LeaveApplications", guid)

    def create_leave_application(self, body):
        return self._create("LeaveApplications", body)

    def update_leave_application(self, guid, body):
        return self._update("LeaveApplications", guid, body)

    # ------------------------------------------------------------------
    # Super funds
    # ------------------------------------------------------------------

    def list_super_funds(self, all_pages=False, limit=None):
        return self._list("SuperFunds", all_pages=all_pages, limit=limit)

    def get_super_fund(self, guid):
        return self._get_one("SuperFunds", guid)

    def create_super_fund(self, body):
        return self._create("SuperFunds", body)

    def update_super_fund(self, guid, body):
        return self._update("SuperFunds", guid, body)

    # ------------------------------------------------------------------
    # Payroll calendars (no update verb in the Payroll AU contract)
    # ------------------------------------------------------------------

    def list_payroll_calendars(self, all_pages=False, limit=None):
        return self._list("PayrollCalendars", all_pages=all_pages, limit=limit)

    def get_payroll_calendar(self, guid):
        return self._get_one("PayrollCalendars", guid)

    def create_payroll_calendar(self, body):
        return self._create("PayrollCalendars", body)

    # ------------------------------------------------------------------
    # Payslip (read-only; singular-object envelope)
    # ------------------------------------------------------------------

    def get_payslip(self, guid):
        return self._one(self.session._get(BASE, f"Payslip/{guid}"))

    # ------------------------------------------------------------------
    # Settings (read-only singleton; singular-object envelope)
    # ------------------------------------------------------------------

    def get_settings(self):
        return self._one(self.session._get(BASE, "Settings"))
