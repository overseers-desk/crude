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
