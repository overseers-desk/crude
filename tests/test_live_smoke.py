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
