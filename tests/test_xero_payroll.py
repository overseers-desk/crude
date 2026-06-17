"""Unit tests for the crude-xero Payroll (payroll.xro/2.0) method group — no network.

These pin the behaviours the Payroll client hinges on against the unified,
camelCase Payroll platform (not the classic PascalCase Payroll AU): the
camelCase-plural-key + `pagination` list-envelope unwrap, the singular-key get
unwrap (a detail wraps the record under its camelCase singular key beside the
envelope metadata), the POST-for-create (Payroll has no PUT) and
POST-to-element-for-update verbs, and the timesheet hard delete. The
`payroll.xro/2.0` base path rides every call. The inner `requests.Session` is
monkeypatched, so nothing reaches the network — the tests target `PayrollAU` with
a mock session directly, not through the client facade.
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
        "acct", "client-id", "client-secret",
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


def _envelope(**kw):
    """Wrap a payload in the Payroll metadata envelope the live API returns."""
    base = {"id": "req-1", "providerName": "test", "dateTimeUTC": "2026-06-17T00:00:00",
            "httpStatusCode": "OK", "problem": None}
    base.update(kw)
    return base


# ----------------------------------------------------------------------
# _one — camelCase singular-key vs one-element-list, beside envelope metadata
# ----------------------------------------------------------------------


def test_one_unwraps_a_singular_key_beside_envelope_metadata():
    api = PayrollAU(_session())
    data = _envelope(pagination={"page": 1}, employee={"employeeID": "e1", "firstName": "Pat"})
    assert api._one(data) == {"employeeID": "e1", "firstName": "Pat"}


def test_one_unwraps_a_one_element_list_under_the_plural_key():
    api = PayrollAU(_session())
    assert api._one({"employees": [{"employeeID": "e1"}], "pagination": {"page": 1}}) == {"employeeID": "e1"}


def test_one_tolerates_odd_shapes():
    api = PayrollAU(_session())
    assert api._one("nope") == {}
    # No list and no lone non-pagination dict -> the whole dict falls through.
    assert api._one({"a": 1, "b": 2}) == {"a": 1, "b": 2}


# ----------------------------------------------------------------------
# Employees — list (paged, plural-key unwrap), get (singular key), create/update verbs
# ----------------------------------------------------------------------


def test_list_employees_unwraps_plural_key_and_pages(monkeypatch):
    payload = _envelope(
        pagination={"page": 1, "pageSize": 100, "pageCount": 1, "itemCount": 1},
        employees=[{"employeeID": "e1"}])
    api, calls = _api(monkeypatch, _FakeResp(payload))

    out = api.list_employees()

    assert out == [{"employeeID": "e1"}]
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/payroll.xro/2.0/Employees")
    assert calls[0]["params"] == {"page": 1}


def test_get_employee_unwraps_singular_key_and_routes(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(
        _envelope(pagination={"page": 1}, employee={"employeeID": "e1", "firstName": "Pat"})))

    out = api.get_employee("e1")

    assert out == {"employeeID": "e1", "firstName": "Pat"}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/payroll.xro/2.0/Employees/e1")


def test_create_employee_is_a_post_to_the_collection(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_envelope(employee={"employeeID": "e1"})))

    api.create_employee({"firstName": "Pat", "lastName": "Lee"})

    assert calls[0]["method"] == "POST"  # not PUT — Payroll has no PUT
    assert calls[0]["url"].endswith("/payroll.xro/2.0/Employees")
    assert calls[0]["json"] == {"firstName": "Pat", "lastName": "Lee"}


def test_update_employee_is_a_post_to_the_element(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_envelope(employee={"employeeID": "e1"})))

    api.update_employee("e1", {"email": "pat@example.com"})

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/payroll.xro/2.0/Employees/e1")
    assert calls[0]["json"] == {"email": "pat@example.com"}


# ----------------------------------------------------------------------
# Pay runs — list (plural unwrap) and create only; no get/update (405/404 routes)
# ----------------------------------------------------------------------


def test_list_pay_runs_unwraps_plural_key(monkeypatch):
    payload = _envelope(
        pagination={"page": 1, "pageSize": 100, "pageCount": 4, "itemCount": 364},
        payRuns=[{"payRunID": "r1", "payRunStatus": "Posted"}])
    api, calls = _api(monkeypatch, _FakeResp(payload))

    out = api.list_pay_runs()

    assert out == [{"payRunID": "r1", "payRunStatus": "Posted"}]
    assert calls[0]["url"].endswith("/payroll.xro/2.0/PayRuns")


def test_create_pay_run_posts_to_collection(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_envelope(payRuns=[{"payRunID": "r1"}])))

    api.create_pay_run({"payrollCalendarID": "c1"})

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/payroll.xro/2.0/PayRuns")


def test_pay_runs_expose_no_get_or_update():
    # The detail/element routes are not served, so the verbs are not modelled.
    api = PayrollAU(_session())
    assert not hasattr(api, "get_pay_run")
    assert not hasattr(api, "update_pay_run")


# ----------------------------------------------------------------------
# Pay run calendars / earnings rates / reimbursements — routing + verbs
# ----------------------------------------------------------------------


def test_pay_run_calendar_get_routes_and_unwraps(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(
        _envelope(pagination={"page": 1}, payRunCalendar={"payrollCalendarID": "c1", "name": "Fortnightly"})))

    out = api.get_pay_run_calendar("c1")

    assert out == {"payrollCalendarID": "c1", "name": "Fortnightly"}
    assert calls[0]["url"].endswith("/payroll.xro/2.0/PayRunCalendars/c1")


def test_earnings_rate_create_and_update_verbs(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(_envelope(earningsRates=[{"earningsRateID": "x1"}])))

    api.create_earnings_rate({"name": "Ordinary"})
    api.update_earnings_rate("x1", {"name": "Ordinary Hours"})

    assert [c["method"] for c in calls] == ["POST", "POST"]
    assert calls[0]["url"].endswith("/payroll.xro/2.0/EarningsRates")
    assert calls[1]["url"].endswith("/payroll.xro/2.0/EarningsRates/x1")


def test_reimbursement_get_routes_and_unwraps(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(
        _envelope(reimbursement={"reimbursementID": "m1", "name": "Travel"})))

    out = api.get_reimbursement("m1")

    assert out == {"reimbursementID": "m1", "name": "Travel"}
    assert calls[0]["url"].endswith("/payroll.xro/2.0/Reimbursements/m1")


# ----------------------------------------------------------------------
# Timesheets — hard delete
# ----------------------------------------------------------------------


def test_delete_timesheet_is_a_delete(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp({}))

    api.delete_timesheet("t1")

    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"].endswith("/payroll.xro/2.0/Timesheets/t1")


# ----------------------------------------------------------------------
# Settings — read-only singleton, singular-key unwrap
# ----------------------------------------------------------------------


def test_get_settings_unwraps_singular_key(monkeypatch):
    api, calls = _api(monkeypatch, _FakeResp(
        _envelope(settings={"accounts": [{"accountID": "a1", "type": "WAGESPAYABLE"}]})))

    out = api.get_settings()

    assert out == {"accounts": [{"accountID": "a1", "type": "WAGESPAYABLE"}]}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/payroll.xro/2.0/Settings")
