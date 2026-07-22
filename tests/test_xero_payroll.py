"""Unit tests for the crude-xero Payroll AU (payroll.xro/1.0) method group — no network.

These pin the behaviours the Payroll AU client hinges on: the plural-key
list-envelope unwrap (a collection arrives under its plural key beside the
`{Id, Status, ProviderName, DateTimeUTC}` envelope scalars), the one-element-list
vs singular-object get unwrap (Payslip/Settings wrap a single object, not a
one-element list), the POST-for-create (Payroll AU has no PUT) and
POST-to-element-for-update verbs, the grouped PayItems object, and the timesheet
hard delete. The `payroll.xro/1.0` base path rides every call. The inner
`requests.Session` is monkeypatched, so nothing reaches the network — the tests
target `PayrollAU` with a mock session directly, not through the client facade.
"""

from __future__ import annotations

import time

from crude_xero.client import XeroSession
from crude_xero.payroll import PayrollAU


class _FakeResp:
    """Stands in for a requests.Response: just the attributes _request reads."""

    def __init__(self, payload=None, *, status_code=200, headers=None, content=None):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.headers = headers or {}
        if content is not None:
            self.content = content
        elif payload is None:
            self.content = b""
        else:
            self.content = b"<body>"  # truthy, so _request calls .json()

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _session(tenant_id="TENANT-1"):
    """A session with a far-future token, so _ensure_token never refreshes."""
    return XeroSession(
        "acct", "client-id",
        {"access_token": "ACCESS-1", "expires_at": time.time() + 9999},
        tenant_id=tenant_id,
    )


def _recorder(response):
    """Capture every transport call; return `response` (or response(call) if callable)."""
    calls = []

    def fake(method, url, params=None, json=None, data=None, headers=None):
        call = {"method": method, "url": url, "params": params,
                "json": json, "data": data, "headers": headers}
        calls.append(call)
        return response(call) if callable(response) else response

    return calls, fake


def _api(monkeypatch, response):
    """A PayrollAU over a mock session whose transport returns `response`."""
    xs = _session()
    api = PayrollAU(xs)
    calls, fake = _recorder(response)
    monkeypatch.setattr(xs.session, "request", fake)
    return api, calls


def _env(**kw):
    """Wrap a payload in the Payroll AU 1.0 metadata envelope the live API returns."""
    base = {"Id": "req-1", "Status": "OK", "ProviderName": "test",
            "DateTimeUTC": "/Date(1700000000000)/"}
    base.update(kw)
    return base


# ----------------------------------------------------------------------
# _one — plural-key one-element list vs singular-object envelope
# ----------------------------------------------------------------------


def test_one_unwraps_a_one_element_list_under_the_plural_key():
    api = PayrollAU(_session())
    assert api._one(_env(Employees=[{"EmployeeID": "e1"}])) == {"EmployeeID": "e1"}


def test_one_unwraps_a_singular_object_envelope():
    api = PayrollAU(_session())
    # Payslip/Settings wrap a single object beside the envelope scalars, not a list.
    assert api._one(_env(Payslip={"PayslipID": "p1"})) == {"PayslipID": "p1"}
    assert api._one(_env(Settings={"DaysInPayrollYear": 365})) == {"DaysInPayrollYear": 365}


def test_one_tolerates_odd_shapes():
    api = PayrollAU(_session())
    assert api._one("nope") == {}
    assert api._one({"a": 1, "b": 2}) == {"a": 1, "b": 2}  # no list, no lone dict -> whole dict


# ----------------------------------------------------------------------
# Employees — list (paged, plural-key unwrap), get, create (POST), update (POST element)
# ----------------------------------------------------------------------


def test_list_employees_unwraps_plural_key_and_pages(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_env(Employees=[{"EmployeeID": "e1"}])))

    out = api.list_employees()

    assert out == [{"EmployeeID": "e1"}]
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/payroll.xro/1.0/Employees")
    assert calls[0]["params"] == {"page": 1}


def test_get_employee_unwraps_and_routes(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_env(Employees=[{"EmployeeID": "e1", "FirstName": "Pat"}])))

    out = api.get_employee("e1")

    assert out == {"EmployeeID": "e1", "FirstName": "Pat"}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/payroll.xro/1.0/Employees/e1")


def test_create_employee_is_a_post_to_the_collection(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_env(Employees=[{"EmployeeID": "e1"}])))

    api.create_employee({"FirstName": "Pat", "LastName": "Lee"})

    assert calls[0]["method"] == "POST"  # not PUT — Payroll AU has no PUT
    assert calls[0]["url"].endswith("/payroll.xro/1.0/Employees")
    assert calls[0]["json"] == {"FirstName": "Pat", "LastName": "Lee"}


def test_update_employee_is_a_post_to_the_element(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_env(Employees=[{"EmployeeID": "e1"}])))

    api.update_employee("e1", {"Status": "ACTIVE"})

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/payroll.xro/1.0/Employees/e1")
    assert calls[0]["json"] == {"Status": "ACTIVE"}


# ----------------------------------------------------------------------
# Pay runs — get (detail carries Payslips), create (POST), update (POST element)
# ----------------------------------------------------------------------


def test_get_pay_run_unwraps_detail_with_payslips(monkeypatch):
    detail = {"PayRunID": "r1", "PayRunStatus": "POSTED",
              "Payslips": [{"PayslipID": "p1", "EmployeeID": "e1"}]}
    api, calls = _api(monkeypatch, _FakeResp(_env(PayRuns=[detail])))

    out = api.get_pay_run("r1")

    assert out == detail  # the single pay run, with its Payslips list intact
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/payroll.xro/1.0/PayRuns/r1")


def test_create_pay_run_posts_to_collection(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_env(PayRuns=[{"PayRunID": "r1"}])))

    api.create_pay_run({"PayrollCalendarID": "c1"})

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/payroll.xro/1.0/PayRuns")


def test_update_pay_run_posts_to_element(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_env(PayRuns=[{"PayRunID": "r1"}])))

    api.update_pay_run("r1", {"PayRunStatus": "POSTED"})

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/payroll.xro/1.0/PayRuns/r1")


# ----------------------------------------------------------------------
# Pay items — grouped object (not a flat list); create/update both POST PayItems
# ----------------------------------------------------------------------


def test_list_pay_items_returns_the_grouped_object(monkeypatch):
    grouped = {"EarningsRates": [{"EarningsRateID": "x1"}], "DeductionTypes": [],
               "LeaveTypes": [], "ReimbursementTypes": []}
    api, calls = _api(monkeypatch, _FakeResp(_env(PayItems=grouped)))

    out = api.list_pay_items()

    assert out == grouped  # the grouped object, NOT unwrapped to a list
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/payroll.xro/1.0/PayItems")


def test_pay_item_create_and_update_post_to_payitems(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_env(PayItems={})))

    api.create_pay_item({"EarningsRates": [{"Name": "Ordinary"}]})
    api.update_pay_item({"EarningsRates": [{"EarningsRateID": "x1", "Name": "Ordinary"}]})

    assert [c["method"] for c in calls] == ["POST", "POST"]
    assert all(c["url"].endswith("/payroll.xro/1.0/PayItems") for c in calls)  # no element path


# ----------------------------------------------------------------------
# Timesheets — hard delete
# ----------------------------------------------------------------------


def test_delete_timesheet_is_a_delete(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp({}))

    api.delete_timesheet("t1")

    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"].endswith("/payroll.xro/1.0/Timesheets/t1")


# ----------------------------------------------------------------------
# Super funds / leave applications / payroll calendars — routing
# ----------------------------------------------------------------------


def test_super_fund_get_routes_to_superfunds(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_env(SuperFunds=[{"SuperFundID": "s1"}])))

    out = api.get_super_fund("s1")

    assert out == {"SuperFundID": "s1"}
    assert calls[0]["url"].endswith("/payroll.xro/1.0/SuperFunds/s1")


def test_create_leave_application_posts(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_env(LeaveApplications=[{"LeaveApplicationID": "l1"}])))

    api.create_leave_application({"EmployeeID": "e1", "LeaveTypeID": "lt1"})

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/payroll.xro/1.0/LeaveApplications")


def test_create_payroll_calendar_posts(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_env(PayrollCalendars=[{"PayrollCalendarID": "c1"}])))

    api.create_payroll_calendar({"Name": "Weekly", "CalendarType": "WEEKLY"})

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/payroll.xro/1.0/PayrollCalendars")


# ----------------------------------------------------------------------
# Payslip & settings — read-only, singular-object unwrap
# ----------------------------------------------------------------------


def test_get_payslip_unwraps_singular_object(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_env(Payslip={"PayslipID": "p1", "NetPay": 1234.5})))

    out = api.get_payslip("p1")

    assert out == {"PayslipID": "p1", "NetPay": 1234.5}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/payroll.xro/1.0/Payslip/p1")


def test_get_settings_unwraps_singular_object(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_env(Settings={"DaysInPayrollYear": 365})))

    out = api.get_settings()

    assert out == {"DaysInPayrollYear": 365}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/payroll.xro/1.0/Settings")
