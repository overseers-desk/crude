"""Unit tests for crude-rezdy's whole-list walks: --all on the list verbs, the
date-window walk of a resource's sessions, the prefix walk of extras, and the
availability list over several products.

The commands run through a CliRunner against a real client whose transport is
the in-memory fake in rezdy_fake.py, so nothing reaches the network. Every record
is made up.
"""

import json
from datetime import date, datetime, timedelta

import pytest
from typer.testing import CliRunner

from crude_rezdy import cli
from crude_rezdy.client import RezdyClient
from rezdy_fake import FakeRezdy, session

runner = CliRunner()


@pytest.fixture
def api(monkeypatch):
    """A client over the fake API, wired in as the CLI's client."""
    fake = FakeRezdy()
    client = RezdyClient("KEY")
    monkeypatch.setattr(client.session, "request", fake.request)
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(
        cli, "read_config",
        lambda _p: {"rezdy": {"api_key": "KEY", "timezone": "Australia/Brisbane"}},
    )
    monkeypatch.setattr(cli, "_make_client", lambda _c: client)
    fake.client = client
    return fake


def run(*args):
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def products(n):
    return [{"productCode": f"P{i:04d}", "name": f"Product {i}", "supplierAlias": "demo"}
            for i in range(n)]


# ---- the walk itself ---------------------------------------------------------


def test_a_walk_ends_on_an_endpoint_that_ignores_offset():
    client = RezdyClient("KEY")
    first_page = [{"id": i} for i in range(100)]
    asked = []

    def fetch(limit, offset):
        asked.append(offset)
        return first_page   # the same 100 however far on one asks

    assert client.walk(fetch) == first_page
    assert asked == [0, 100]   # the second, all-repeat page ended it


def test_a_walk_keeps_a_record_seen_on_two_pages_once():
    client = RezdyClient("KEY")
    pages = {0: [{"id": 1}, {"id": 2}], 2: [{"id": 2}, {"id": 3}], 4: [{"id": 4}]}
    got = client.walk(lambda limit, offset: pages.get(offset, []), page=2)
    assert [r["id"] for r in got] == [1, 2, 3, 4]


# ---- --all on the list verbs that page ---------------------------------------


def test_product_list_all_walks_every_page(api):
    api.products = products(250)
    got = run("product", "list", "--all", "--json")
    assert [p["productCode"] for p in got] == [p["productCode"] for p in api.products]
    assert [c[2]["offset"] for c in api.calls] == [0, 100, 200]
    assert {c[2]["limit"] for c in api.calls} == {100}


def test_product_list_without_all_still_reads_one_page_of_twenty(api):
    api.products = products(250)
    got = run("product", "list", "--json")
    assert len(got) == 20
    assert len(api.calls) == 1


def test_product_list_all_keeps_the_search(api):
    api.products = products(250)
    got = run("product", "list", "--all", "--search", "Product 24", "--json")
    assert {p["name"] for p in got} == {"Product 24", "Product 240", "Product 241", "Product 242",
                                        "Product 243", "Product 244", "Product 245", "Product 246",
                                        "Product 247", "Product 248", "Product 249"}


def test_customer_list_all(api):
    api.customers = [{"id": i, "firstName": "Test", "lastName": f"Person{i}"} for i in range(130)]
    got = run("customer", "list", "--all", "--json")
    assert len(got) == 130


def test_voucher_list_all_without_ids(api):
    # Vouchers carry a code, not an id: the walk tells them apart by the record.
    api.vouchers = [{"code": f"V{i:04d}", "status": "VALID"} for i in range(205)]
    got = run("voucher", "list", "--all", "--json")
    assert [v["code"] for v in got] == [v["code"] for v in api.vouchers]


def test_category_list_all_keeps_the_visibility_filter(api):
    api.categories = [{"id": i, "name": f"C{i}", "visible": i % 2 == 0} for i in range(300)]
    got = run("category", "list", "--all", "--private", "--json")
    assert len(got) == 150
    assert all(c[2]["visible"] == "false" for c in api.calls)


def test_category_products_all_and_resource_list_all(api):
    api.category_products["7"] = products(120)
    api.resources = [{"id": i, "name": f"R{i}"} for i in range(101)]
    assert len(run("category", "products", "7", "--all", "--json")) == 120
    assert len(run("resource", "list", "--all", "--json")) == 101


# ---- availability over several products --------------------------------------


def day_sessions(code, n, first_id=1):
    start = datetime(2026, 11, 1, 9, 0, 0)
    out = []
    for i in range(n):
        at = start + timedelta(minutes=i)
        out.append(session(first_id + i, at.strftime("%Y-%m-%d %H:%M:%S"), product=code))
    return out


WINDOW = ["--from", "2026-11-01 00:00:00", "--to", "2026-11-30 23:59:59"]


def test_availability_list_one_product_is_as_it_was(api):
    api.availability["PA"] = day_sessions("PA", 3)
    result = runner.invoke(cli.app, ["availability", "list", "--product", "PA", *WINDOW, "--json"])
    assert result.exit_code == 0
    assert [s["id"] for s in json.loads(result.output)] == [1, 2, 3]
    assert api.calls[0][2]["limit"] == 100 and api.calls[0][2]["offset"] == 0


def test_availability_list_repeats_product(api):
    api.availability["PA"] = day_sessions("PA", 2)
    api.availability["PB"] = day_sessions("PB", 2, first_id=10)
    got = run("availability", "list", "--product", "PA", "--product", "PB", *WINDOW, "--json")
    assert [(s["productCode"], s["id"]) for s in got] == [("PA", 1), ("PA", 2), ("PB", 10), ("PB", 11)]


def test_availability_list_all_products_walks_the_catalogue(api):
    api.products = products(3)
    for p in api.products:
        api.availability[p["productCode"]] = day_sessions(p["productCode"], 2, first_id=100 * (1 + api.products.index(p)))
    got = run("availability", "list", "--all-products", *WINDOW, "--json")
    assert sorted({s["productCode"] for s in got}) == ["P0000", "P0001", "P0002"]
    assert len(got) == 6


def test_availability_list_all_pages_a_product_by_the_thousand(api):
    api.availability["PA"] = day_sessions("PA", 1500)
    got = run("availability", "list", "--product", "PA", "--all", *WINDOW, "--json")
    assert len(got) == 1500
    assert [c[2]["offset"] for c in api.calls] == [0, 1000]
    assert {c[2]["limit"] for c in api.calls} == {1000}


@pytest.mark.parametrize("flags", [[], ["--product", "PA", "--all-products"]])
def test_availability_list_needs_exactly_one_way_to_name_products(api, flags):
    result = runner.invoke(cli.app, ["availability", "list", *flags, *WINDOW])
    assert result.exit_code == 2
    assert api.calls == []


# ---- resource sessions, walked by date ---------------------------------------


def packed(first_id, day, n, minutes=1):
    """n one-minute sessions on one day, a minute apart from 08:00."""
    out = []
    for i in range(n):
        at = datetime.combine(day, datetime.min.time()) + timedelta(hours=8, minutes=i * minutes)
        out.append(session(first_id + i, at.strftime("%Y-%m-%d %H:%M:%S"),
                           (at + timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")))
    return out


def test_resource_sessions_all_walks_past_100_and_keeps_each_once(api):
    rows = []
    # 90 on each of three days in March: 270 in a month, which one call cannot hold.
    for k, day in enumerate((date(2026, 3, 3), date(2026, 3, 12), date(2026, 3, 27))):
        rows += packed(1000 * (k + 1), day, 90)
    rows += packed(9000, date(2026, 4, 20), 10)   # April
    api.sessions_by_resource["5"] = rows
    got = run("resource", "sessions", "5", "--from", "2026-03-01", "--to", "2026-04-30", "--all", "--json")
    assert sorted(s["id"] for s in got) == sorted(s["id"] for s in rows)
    assert len({s["id"] for s in got}) == len(got) == 280
    # In start order.
    assert [s["startTimeLocal"] for s in got] == sorted(s["startTimeLocal"] for s in got)
    # No page of the walk leans on offset: Rezdy ignores it.
    assert all(c[2]["limit"] == 100 for c in api.calls)


def test_resource_sessions_all_loses_no_session_running_over_a_midnight(api):
    # Two busy days make March too full for one call, so it is cut in halves. One
    # session runs over every midnight of the month (the last into April), so
    # whichever midnight a cut falls on, a session straddles it.
    rows = packed(1, date(2026, 3, 5), 70) + packed(1000, date(2026, 3, 20), 70)
    for d in range(1, 32):
        night = date(2026, 3, d)
        rows.append(session(5000 + d, f"{night} 23:30:00",
                            f"{night + timedelta(days=1)} 00:30:00"))
    api.sessions_by_resource["5"] = rows
    got = run("resource", "sessions", "5", "--from", "2026-03-01", "--to", "2026-04-30", "--all", "--json")
    assert sorted(s["id"] for s in got) == sorted(s["id"] for s in rows)
    assert len(api.calls) > 2   # it did have to cut


def test_resource_sessions_all_cuts_below_a_day_when_a_day_holds_over_100(api):
    # 150 sessions a day, 5 minutes apart, is more than one call returns for a
    # single day; the day itself has to be cut, by time.
    rows = packed(1, date(2026, 3, 10), 150, minutes=5) + packed(1000, date(2026, 3, 11), 150, minutes=5)
    api.sessions_by_resource["5"] = rows
    result = runner.invoke(cli.app, ["resource", "sessions", "5", "--from", "2026-03-01",
                                     "--to", "2026-03-31", "--all", "--json"])
    assert result.exit_code == 0, result.output
    assert sorted(s["id"] for s in json.loads(result.output)) == sorted(s["id"] for s in rows)


def test_resource_sessions_all_margin_follows_the_longest_session_seen(api):
    # Six-hour sessions starting every half hour for a week: whatever cut is made,
    # a dozen sessions run over it, and each must lie whole in one half.
    rows, sid = [], 1
    for step in range(7 * 48):
        start = datetime(2026, 3, 9, 0, 0, 0) + timedelta(minutes=30 * step)
        rows.append(session(sid, start.strftime("%Y-%m-%d %H:%M:%S"),
                            (start + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")))
        sid += 1
    api.sessions_by_resource["5"] = rows
    result = runner.invoke(cli.app, ["resource", "sessions", "5", "--from", "2026-03-01",
                                     "--to", "2026-03-31", "--all", "--json"])
    assert result.exit_code == 0, result.output
    assert sorted(s["id"] for s in json.loads(result.output)) == sorted(s["id"] for s in rows)


def test_resource_sessions_all_stays_inside_the_typed_window(api):
    api.sessions_by_resource["5"] = [session(1, "2026-02-28 10:00:00"), session(2, "2026-03-05 10:00:00"),
                                     session(3, "2026-03-31 23:30:00", "2026-04-01 00:30:00")]
    got = run("resource", "sessions", "5", "--from", "2026-03-01", "--to", "2026-03-31", "--all", "--json")
    # Session 3 ends after the window, which Rezdy leaves out of a single call too.
    assert [s["id"] for s in got] == [2]


def test_resource_sessions_all_reports_a_window_it_cannot_cut(api):
    api.sessions_by_resource["5"] = packed(1, date(2026, 3, 10), 120)
    result = runner.invoke(cli.app, ["resource", "sessions", "5", "--from", "2026-03-01",
                                     "--to", "2026-03-31", "--all", "--json"])
    assert result.exit_code == 1
    assert "sessions are missing" in result.output


def test_resource_sessions_all_needs_a_window(api):
    result = runner.invoke(cli.app, ["resource", "sessions", "5", "--all"])
    assert result.exit_code == 1
    assert "--from and --to" in result.output
    assert api.calls == []


def test_resource_sessions_without_all_is_one_call_and_says_offset_is_ignored(api):
    api.sessions_by_resource["5"] = packed(1, date(2026, 3, 10), 120)
    result = runner.invoke(cli.app, ["resource", "sessions", "5", "--from", "2026-03-01",
                                     "--to", "2026-03-31", "--offset", "100", "--json"])
    assert result.exit_code == 0
    assert len(api.calls) == 1
    assert "ignores --offset" in result.output and "at most 100" in result.output


# ---- extras ------------------------------------------------------------------


def extras(n):
    letters = "abcdefghij"
    return [{"id": 1000 + i, "name": f"{letters[i % 10].upper()}extra {i}", "price": 5}
            for i in range(n)]


def test_extra_list_reads_one_capped_page_and_says_so(api):
    api.extras = extras(230)
    result = runner.invoke(cli.app, ["extra", "list", "--json"])
    assert result.exit_code == 0
    assert "add --all" in result.output
    assert len(api.calls) == 1


def test_extra_list_all_reaches_every_extra_by_prefix(api):
    api.extras = extras(230)
    got = run("extra", "list", "--all", "--json")
    assert sorted(e["id"] for e in got) == sorted(e["id"] for e in api.extras)
    # The API was only ever asked for a search string: it has no paging to ask for.
    assert all(set(c[2]) == {"searchString"} for c in api.calls)


def test_extra_list_all_with_a_search_walks_inside_it(api):
    api.extras = extras(230)
    got = run("extra", "list", "--all", "--search", "a", "--json")
    assert {e["name"][0] for e in got} == {"A"}
    assert len(got) == 23


def test_extras_named_finds_one_the_prefix_walk_cannot(api):
    api.extras = extras(230) + [{"id": 1, "name": "(odd) Extra", "price": 1}]
    walked, _capped = api.client.all_extras()
    assert "(odd) Extra" not in {e["name"] for e in walked}
    assert [e["id"] for e in api.client.extras_named(["(odd) Extra", "(odd) Extra", None])] == [1]


def test_all_extras_reports_a_prefix_it_cannot_split(api):
    api.extras = [{"id": i, "name": "z" * 12, "price": 1} for i in range(1, 150)]
    walked, capped = api.client.all_extras()
    assert capped and capped[0].startswith("z")
