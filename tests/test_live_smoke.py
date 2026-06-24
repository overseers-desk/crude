"""Live smoke tests: one object of each type from each site.

These prove the thing most likely to rot in crude: that authentication still
works and one reverse-engineered endpoint per site still answers. They are not
run by default. Enable them deliberately, with a populated config in place:

    pytest -m live

Each test reuses the CLI's own client construction (``_make_client``), so it
exercises the real auth path rather than a parallel one. A test skips when its
site's credentials are absent (nothing to test); it fails when credentials are
present but the round trip does not come back, which is the signal worth having.
Assertions check shape, not content, since an account's contents vary: the call
returns a list, and a non-empty list carries the site's identifying field.
"""

import pytest


@pytest.mark.live
def test_atdw_lists_one_listing(crude_config):
    if not crude_config.get("atdw", {}).get("username"):
        pytest.skip("no [atdw] credentials in config")
    from crude_atdw.cli import _make_client

    client = _make_client(crude_config)
    items = client.list_listings(limit=1)
    assert isinstance(items, list)
    if items:
        assert items[0].get("id")


@pytest.mark.live
def test_skal_lists_one_member(crude_config):
    skal = crude_config.get("skal", {})
    if not (skal.get("username") or skal.get("session_id")):
        pytest.skip("no [skal] credentials in config")
    from crude_skal.cli import _make_client

    client = _make_client(crude_config)
    items = client.list_members(limit=1)
    assert isinstance(items, list)
    if items:
        assert items[0].get("id")


@pytest.mark.live
def test_skal_lists_one_benefit(crude_config):
    skal = crude_config.get("skal", {})
    if not (skal.get("username") or skal.get("session_id")):
        pytest.skip("no [skal] credentials in config")
    from crude_skal.cli import _make_client

    client = _make_client(crude_config)
    items = client.list_benefits(limit=1)
    assert isinstance(items, list)
    if items:
        assert items[0].get("id")


@pytest.mark.live
def test_rezdy_lists_one_product(crude_config):
    if not crude_config.get("rezdy", {}).get("api_key"):
        pytest.skip("no [rezdy] api_key in config")
    from crude_rezdy.cli import _make_client

    client = _make_client(crude_config)
    items = client.list_products(limit=1)
    assert isinstance(items, list)
    if items:
        assert items[0].get("productCode")


@pytest.mark.live
def test_rezdy_lists_cancelled_bookings(crude_config):
    if not crude_config.get("rezdy", {}).get("api_key"):
        pytest.skip("no [rezdy] api_key in config")
    from crude_rezdy.cli import _make_client

    client = _make_client(crude_config)
    items = client.list_bookings(order_status="CANCELLED", limit=5)
    assert isinstance(items, list)
    for b in items:
        assert b.get("status") == "CANCELLED"


@pytest.mark.live
def test_rezdy_paginate(crude_config):
    if not crude_config.get("rezdy", {}).get("api_key"):
        pytest.skip("no [rezdy] api_key in config")
    from crude_rezdy.cli import _make_client

    client = _make_client(crude_config)
    items = client.paginate(limit=10, order_status="CANCELLED")
    assert isinstance(items, list)
    single_page = client.list_bookings(order_status="CANCELLED", limit=10)
    assert len(items) >= len(single_page)


@pytest.mark.live
def test_rezdy_lists_vouchers(crude_config):
    # Read-only; the API does not allow creating these (see docs/rezdy.md). The
    # endpoint is a search needing a (possibly empty) term, exercised here.
    if not crude_config.get("rezdy", {}).get("api_key"):
        pytest.skip("no [rezdy] api_key in config")
    from crude_rezdy.cli import _make_client

    client = _make_client(crude_config)
    items = client.list_vouchers(search="", limit=1)
    assert isinstance(items, list)
    if items:
        assert items[0].get("code")


@pytest.mark.live
def test_rezdy_corrected_read_endpoints(crude_config):
    # These paths differ from the obvious guess (singular /extra, /pickups,
    # /rates/search, /resources); each returning a list proves the path is right.
    if not crude_config.get("rezdy", {}).get("api_key"):
        pytest.skip("no [rezdy] api_key in config")
    from crude_rezdy.cli import _make_client

    client = _make_client(crude_config)
    assert isinstance(client.list_extras(), list)
    assert isinstance(client.list_pickup_lists(), list)
    assert isinstance(client.list_rates(), list)
    assert isinstance(client.list_categories(limit=1), list)
    assert isinstance(client.list_resources(limit=1), list)


@pytest.mark.live
def test_deputy_me(crude_config):
    if not crude_config.get("deputy", {}).get("deputy_api_token"):
        pytest.skip("no [deputy] credentials in config")
    from crude_deputy.cli import _make_client

    client = _make_client(crude_config)
    me = client.me()
    assert isinstance(me, dict)
    assert me.get("Id") or me.get("UserId")


@pytest.mark.live
def test_deputy_lists_one_employee(crude_config):
    if not crude_config.get("deputy", {}).get("deputy_api_token"):
        pytest.skip("no [deputy] credentials in config")
    from crude_deputy.cli import _make_client

    client = _make_client(crude_config)
    items = client.list_resource("Employee", max_=1)
    assert isinstance(items, list)
    if items:
        assert items[0].get("Id")


@pytest.mark.live
def test_sonas_lists_one_event(crude_config):
    if not crude_config.get("sonas", {}).get("username"):
        pytest.skip("no [sonas] credentials in config")
    from crude_sonas.cli import _make_client

    client = _make_client(crude_config)
    try:
        events = client.list_events()
        assert isinstance(events, list)
        if events:
            assert events[0].get("_id")
    finally:
        client.close()


@pytest.mark.live
def test_sonas_event_detail_pub(crude_config):
    if not crude_config.get("sonas", {}).get("username"):
        pytest.skip("no [sonas] credentials in config")
    from crude_sonas.cli import _make_client

    client = _make_client(crude_config)
    try:
        events = client.list_events()
        if not events:
            pytest.skip("account has no events to read a detail pub against")
        guests = client.read_pub("guests", [events[0]["_id"]])
        assert isinstance(guests, list)
    finally:
        client.close()


@pytest.mark.live
def test_sonas_tabular_read(crude_config):
    if not crude_config.get("sonas", {}).get("username"):
        pytest.skip("no [sonas] credentials in config")
    from crude_sonas.cli import _make_client

    client = _make_client(crude_config)
    try:
        # ServiceList defines no custom data pub; aldeed:tabular's built-in
        # tabular_genericPub(tableName, ids, projection) serves it (collection
        # auto-detected as "services").
        rows, info = client.read_tabular("ServiceList", data_pub="tabular_genericPub")
        assert isinstance(rows, list)
        assert isinstance(info["recordsTotal"], int)
        if rows:
            assert rows[0].get("_id")
    finally:
        client.close()


@pytest.mark.live
def test_xero_reads_organisation(crude_config):
    xero = crude_config.get("xero", {})
    if not (xero.get("client_id") and xero.get("client_secret")):
        pytest.skip("no [xero] credentials in config")
    from crude_xero.cli import _make_client

    client = _make_client(crude_config)
    org = client.accounting.get_organisation()
    assert isinstance(org, dict)
    assert org.get("OrganisationID") or org.get("Name")


@pytest.mark.live
def test_xero_lists_accounts(crude_config):
    # Accounts sit under accounting.settings(.read); also exercises the paging
    # walk's stop on an unpaged collection (Accounts ignores the page param).
    xero = crude_config.get("xero", {})
    if not (xero.get("client_id") and xero.get("client_secret")):
        pytest.skip("no [xero] credentials in config")
    from crude_xero.cli import _make_client

    client = _make_client(crude_config)
    items = client.accounting.list_accounts()
    assert isinstance(items, list)
    if items:
        assert items[0].get("AccountID")


def _xero_payroll_or_skip(crude_config):
    """A Xero client for a payroll read, skipping when creds or scope are absent.

    The Payroll product needs both [xero] credentials and a granted `payroll.*`
    scope; without the scope the read returns 401/403, which is a skip (nothing
    to test here), not a failure.
    """
    xero = crude_config.get("xero", {})
    if not (xero.get("client_id") and xero.get("client_secret")):
        pytest.skip("no [xero] credentials in config")
    from crude_xero.cli import _make_client

    return _make_client(crude_config)


@pytest.mark.live
def test_xero_lists_payroll_employees(crude_config):
    from crude_xero.client import XeroError

    client = _xero_payroll_or_skip(crude_config)
    try:
        items = client.payroll.list_employees()
    except XeroError as e:
        if e.status in (401, 403):
            pytest.skip("token lacks the payroll scope")
        raise
    assert isinstance(items, list)
    if items:
        assert items[0].get("EmployeeID")


@pytest.mark.live
def test_xero_lists_pay_runs(crude_config):
    from crude_xero.client import XeroError

    client = _xero_payroll_or_skip(crude_config)
    try:
        items = client.payroll.list_pay_runs()
    except XeroError as e:
        if e.status in (401, 403):
            pytest.skip("token lacks the payroll scope")
        raise
    assert isinstance(items, list)
    if items:
        assert items[0].get("PayRunID")


@pytest.mark.live
def test_xero_pay_run_detail_carries_payslips(crude_config):
    # The pay-run detail (GET PayRuns/{id}) carries the run's payslips: the
    # per-employee paid-this-run data, reachable on payroll.xro/1.0.
    from crude_xero.client import XeroError

    client = _xero_payroll_or_skip(crude_config)
    try:
        runs = client.payroll.list_pay_runs()
        if not runs:
            pytest.skip("no pay runs to read a detail against")
        detail = client.payroll.get_pay_run(runs[0]["PayRunID"])
    except XeroError as e:
        if e.status in (401, 403):
            pytest.skip("token lacks the payroll scope")
        raise
    payslips = detail.get("Payslips")
    assert isinstance(payslips, list)
    if payslips:
        assert payslips[0].get("PayslipID") and payslips[0].get("EmployeeID")


@pytest.mark.live
def test_airwallex_lists_current_balances(crude_config):
    # Exercises the real api-key login path and the snake_case balances envelope.
    if not crude_config.get("airwallex", {}).get("client_id"):
        pytest.skip("no [airwallex] credentials in config")
    from crude_airwallex.cli import _make_client

    client = _make_client(crude_config)
    items = client.core.list_current_balances()
    assert isinstance(items, list)
    if items:
        assert items[0].get("currency")


@pytest.mark.live
def test_airwallex_lists_one_transaction(crude_config):
    # The financial_transactions endpoint returns camelCase fields (id, createdAt).
    if not crude_config.get("airwallex", {}).get("client_id"):
        pytest.skip("no [airwallex] credentials in config")
    from crude_airwallex.cli import _make_client

    client = _make_client(crude_config)
    items = client.core.list_financial_transactions(limit=1)
    assert isinstance(items, list)
    if items:
        assert items[0].get("id")


@pytest.mark.live
def test_airwallex_lists_one_beneficiary(crude_config):
    # Payouts beneficiaries: snake_case, with the bank details under a nested
    # `beneficiary` object and the id at `beneficiary_id`.
    if not crude_config.get("airwallex", {}).get("client_id"):
        pytest.skip("no [airwallex] credentials in config")
    from crude_airwallex.cli import _make_client

    client = _make_client(crude_config)
    items = client.beneficiaries.list_beneficiaries(limit=1)
    assert isinstance(items, list)
    if items:
        assert items[0].get("beneficiary_id")


@pytest.mark.live
def test_airwallex_lists_one_conversion(crude_config):
    # FX conversions are date-versioned: the endpoint 400s without x-api-version,
    # which FxAPI sends. snake_case id at `conversion_id`.
    if not crude_config.get("airwallex", {}).get("client_id"):
        pytest.skip("no [airwallex] credentials in config")
    from crude_airwallex.cli import _make_client

    client = _make_client(crude_config)
    items = client.fx.list_conversions(limit=1)
    assert isinstance(items, list)
    if items:
        assert items[0].get("conversion_id")


@pytest.mark.live
def test_airwallex_lists_transfers(crude_config):
    # Read-only: the list shape, not content (the account may have no transfers).
    if not crude_config.get("airwallex", {}).get("client_id"):
        pytest.skip("no [airwallex] credentials in config")
    from crude_airwallex.cli import _make_client

    client = _make_client(crude_config)
    items = client.transfers.list_transfers(limit=1)
    assert isinstance(items, list)
    if items:
        assert items[0].get("id")


def _airwallex_pa_or_skip(crude_config):
    """A client for a Payments Acceptance read, skipping when creds or pa are absent.

    The `pa` group needs the Payments Acceptance product enabled on the account;
    a disabled account answers with a 401/403/404, which is a skip (nothing to test
    here), not a failure. An enabled account answers with a (possibly empty) page.
    """
    if not crude_config.get("airwallex", {}).get("client_id"):
        pytest.skip("no [airwallex] credentials in config")
    from crude_airwallex.cli import _make_client

    return _make_client(crude_config)


@pytest.mark.live
def test_airwallex_lists_pa_customers(crude_config):
    # Payments Acceptance customers: snake_case `id`. Shape-only; tolerates empty
    # (an account using pa only for inbound links may have no saved customers).
    from crude_airwallex.client import AirwallexError

    client = _airwallex_pa_or_skip(crude_config)
    try:
        items = client.payments.list_customers(limit=1)
    except AirwallexError as e:
        if e.status in (401, 403, 404):
            pytest.skip("Payments Acceptance not enabled on this account")
        raise
    assert isinstance(items, list)
    if items:
        assert items[0].get("id")


@pytest.mark.live
def test_airwallex_lists_pa_payment_intents(crude_config):
    # Payments Acceptance payment intents: snake_case `id` (e.g. int_...). Shape-only;
    # tolerates empty.
    from crude_airwallex.client import AirwallexError

    client = _airwallex_pa_or_skip(crude_config)
    try:
        items = client.payments.list_payment_intents(limit=1)
    except AirwallexError as e:
        if e.status in (401, 403, 404):
            pytest.skip("Payments Acceptance not enabled on this account")
        raise
    assert isinstance(items, list)
    if items:
        assert items[0].get("id")


# ---------------------------------------------------------------------------
# crude-facebook (graph.facebook.com)
#
# The command logic (field sets, list->id chaining) lives in the cli modules, so
# these drive the real CLI commands through a CliRunner to exercise each branch as
# the user runs it. A test skips when [facebook] access_token is absent; it fails
# when the token is present but a read does not come back, which is the signal
# worth having. The write round-trip mutates the live Page, so it is gated behind
# an env flag and is reversible (it only ever touches content it creates).
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402
import os as _os  # noqa: E402

from typer.testing import CliRunner as _CliRunner  # noqa: E402

_fb_runner = _CliRunner()

_FB_WRITES = _os.environ.get("CRUDE_FACEBOOK_LIVE_WRITES") == "1"


def _fb_or_skip(crude_config):
    if not crude_config.get("facebook", {}).get("access_token"):
        pytest.skip("no [facebook] access_token in config")


def _fb_json(args):
    """Invoke a crude-facebook command with --json; fail (not skip) on a non-zero exit."""
    from crude_facebook.cli import app

    r = _fb_runner.invoke(app, args + ["--json"])
    assert r.exit_code == 0, f"{' '.join(args)} -> exit {r.exit_code}\n{r.output}"
    return _json.loads(r.output)


@pytest.fixture(scope="session")
def _fb_post_id(crude_config):
    _fb_or_skip(crude_config)
    data = _fb_json(["post", "list", "--limit", "1"])
    if not data:
        pytest.skip("Page has no published posts")
    return data[0]["id"]


# --- reads (must pass when the token is present) ---------------------------

@pytest.mark.live
def test_facebook_status(crude_config):
    _fb_or_skip(crude_config)
    assert "page_id" in _fb_json(["status"])


@pytest.mark.live
def test_facebook_page_get(crude_config):
    _fb_or_skip(crude_config)
    rec = _fb_json(["page", "get"])
    assert rec.get("id")
    print("FB page about:", repr(rec.get("about")))


@pytest.mark.live
def test_facebook_page_insights(crude_config):
    _fb_or_skip(crude_config)
    assert isinstance(_fb_json(["page", "insights"]), list)


@pytest.mark.live
def test_facebook_post_list(crude_config):
    _fb_or_skip(crude_config)
    data = _fb_json(["post", "list", "--limit", "5"])
    assert isinstance(data, list)
    for p in data:
        print("FB post:", p.get("id"), "|", repr((p.get("message") or "")[:90]))


@pytest.mark.live
def test_facebook_post_get(crude_config, _fb_post_id):
    assert _fb_json(["post", "get", _fb_post_id]).get("id")


@pytest.mark.live
def test_facebook_post_insights(crude_config, _fb_post_id):
    assert isinstance(_fb_json(["post", "insights", _fb_post_id]), list)


@pytest.mark.live
def test_facebook_comment_list(crude_config, _fb_post_id):
    assert isinstance(_fb_json(["comment", "list", _fb_post_id]), list)


# --- writes (reversible, opt-in; touch only content they create) ----------

@pytest.mark.live
@pytest.mark.skipif(not _FB_WRITES, reason="set CRUDE_FACEBOOK_LIVE_WRITES=1 to run live FB writes")
def test_facebook_write_roundtrip(crude_config):
    """create post -> comment -> hide -> unhide -> delete comment -> edit -> delete."""
    _fb_or_skip(crude_config)
    post = _fb_json(["post", "create", "-m", "crude-facebook self-test, ignore", "--yes"])
    pid = post.get("id")
    assert pid
    try:
        c = _fb_json(["comment", "reply", pid, "-m", "self-test", "--yes"])
        cid = c.get("id")
        if cid:
            _fb_json(["comment", "hide", cid, "--yes"])
            _fb_json(["comment", "unhide", cid, "--yes"])
            _fb_json(["comment", "delete", cid, "--yes"])
        _fb_json(["post", "edit", pid, "-m", "crude-facebook self-test edited, ignore", "--yes"])
    finally:
        _fb_json(["post", "delete", pid, "--yes"])
