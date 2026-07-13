"""Per-backend WORLD_AS_OF boundaries: server-side clamps where the backend
offers a filter, exact client-side post-filters where it does not, and the
mutated-after-cutoff flag. No network: transports are monkeypatched with the
repo's standard fake-response pattern, CLI bodies get stub clients.

Every backend test includes the design's guard case: a record whose business
date is in the future but whose creation is before the cutoff stays visible —
the bound acts on knowledge time, never on the domain timeline.
"""

from __future__ import annotations

import time

import pytest

from crude_common import asof

BOUND = "2026-07-12T17:07:00+10:00"       # 2026-07-12T07:07:00Z
BOUND_Z = "2026-07-12T07:07:00Z"
BOUND_MS = 1783840020000                  # the same instant in UTC epoch ms

BEFORE = "2026-07-01T00:00:00Z"
AFTER = "2026-07-13T00:00:00Z"


@pytest.fixture
def bound(monkeypatch):
    monkeypatch.setenv(asof.ENV, BOUND)


class _FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = {} if body is None else body
        self.headers = {}
        self.content = b"x"

    def json(self):
        return self._body


# ----------------------------------------------------------------------
# Airwallex: to_created_at clamped server-side; created/updated post-filter
# ----------------------------------------------------------------------


def _awx_session():
    from crude_airwallex import auth
    from crude_airwallex.client import AirwallexSession

    return AirwallexSession("acct", "cid", "key", base=auth.PROD_BASE,
                            token={"token": "TOK", "expires_at": time.time() + 9999})


def test_airwallex_transactions_clamped_and_filtered(bound, monkeypatch, capsys):
    from crude_airwallex.core import CoreAPI

    sess = _awx_session()
    seen = {}
    rows = [
        {"id": "t1", "createdAt": BEFORE, "settledAt": BEFORE},
        {"id": "t2", "createdAt": BEFORE, "settledAt": AFTER},   # settled post-cutoff
        {"id": "t3", "createdAt": AFTER},                        # leaked past the server filter
    ]

    def fake(method, url, **kw):
        seen.update(params=kw.get("params"))
        return _FakeResp(body={"items": rows, "has_more": False})

    monkeypatch.setattr(sess.session, "request", fake)
    items = CoreAPI(sess).list_financial_transactions()
    assert seen["params"]["to_created_at"] == BOUND_Z          # bound injected server-side
    assert [r["id"] for r in items] == ["t1", "t2"]            # created-after dropped
    assert items[1][asof.MARKER_KEY] == asof.MUTATED           # settled-after flagged
    assert "WORLD_AS_OF" in capsys.readouterr().err


def test_airwallex_user_upper_bound_is_clamped_not_replaced(bound, monkeypatch):
    from crude_airwallex.transfers import TransfersAPI

    sess = _awx_session()
    seen = {}

    def fake(method, url, **kw):
        seen.update(params=kw.get("params"))
        return _FakeResp(body={"items": [], "has_more": False})

    monkeypatch.setattr(sess.session, "request", fake)
    TransfersAPI(sess).list_transfers(to=BEFORE)               # earlier than the bound
    assert seen["params"]["to_created_at"] == BEFORE           # user's tighter bound wins
    TransfersAPI(sess).list_transfers(to=AFTER)                # later than the bound
    assert seen["params"]["to_created_at"] == BOUND_Z          # clamped


def test_airwallex_window_after_cutoff_refuses(bound, monkeypatch):
    import typer

    from crude_airwallex.beneficiaries import BeneficiariesAPI

    sess = _awx_session()
    monkeypatch.setattr(sess.session, "request",
                        lambda *a, **k: pytest.fail("refused window reached the network"))
    with pytest.raises(typer.Exit):
        BeneficiariesAPI(sess).list_beneficiaries(from_=AFTER)


def test_airwallex_now_valued_reads_refuse(bound, monkeypatch):
    import typer

    from crude_airwallex import cli_core, cli_fx

    monkeypatch.setattr(cli_core, "_client",
                        lambda: pytest.fail("refused read built a client"))
    with pytest.raises(typer.Exit):
        cli_core.balance_current(output_json=False)
    monkeypatch.setattr(cli_fx, "_client",
                        lambda: pytest.fail("refused read built a client"))
    with pytest.raises(typer.Exit):
        cli_fx.fx_rate_current(buy="USD", sell="AUD", amount=None, output_json=False)


# ----------------------------------------------------------------------
# Clover: orders windows clamped server-side (createdTime<=); rows flagged
# ----------------------------------------------------------------------


def _clover_session():
    from crude_clover.client import CloverSession

    sess = CloverSession("token")
    sess._merchant_id = "M1"
    return sess


def test_clover_order_window_clamped_to_bound(bound, monkeypatch):
    from crude_clover.orders import OrdersAPI

    sess = _clover_session()
    seen = []

    def fake(method, url, **kw):
        seen.append(kw.get("params"))
        return _FakeResp(body={"elements": []})

    monkeypatch.setattr(sess.session, "request", fake)
    list(OrdersAPI(sess).iter_orders(BOUND_MS - 10_000, BOUND_MS + 10_000))
    filters = [v for k, v in seen[0] if k == "filter"]
    assert f"createdTime<={BOUND_MS}" in filters               # upper edge clamped


def test_clover_window_entirely_after_bound_never_calls(bound, monkeypatch):
    from crude_clover.orders import OrdersAPI

    sess = _clover_session()
    monkeypatch.setattr(sess.session, "request",
                        lambda *a, **k: pytest.fail("post-cutoff window reached the network"))
    assert list(OrdersAPI(sess).iter_orders(BOUND_MS + 1, BOUND_MS + 10_000)) == []


def test_clover_pull_range_drops_and_flags_rows(bound, monkeypatch, tmp_path):
    import json

    from types import SimpleNamespace

    from crude_clover.cli_orders import _pull_range

    rows = [
        {"id": "o1", "createdTime": BOUND_MS - 100, "modifiedTime": BOUND_MS - 50},
        {"id": "o2", "createdTime": BOUND_MS - 100, "modifiedTime": BOUND_MS + 50},
        {"id": "o3", "createdTime": BOUND_MS + 100, "modifiedTime": BOUND_MS + 100},
    ]
    client = SimpleNamespace(orders=SimpleNamespace(iter_orders=lambda s, e: iter(rows)))
    out = tmp_path / "orders.jsonl"
    total = _pull_range(client, "2026-07-12", "2026-07-12", "Australia/Brisbane", str(out))
    written = [json.loads(line) for line in out.read_text().splitlines()]
    assert total == 2
    assert [o["id"] for o in written] == ["o1", "o2"]
    assert asof.MARKER_KEY not in written[0]
    assert written[1][asof.MARKER_KEY] == asof.MUTATED


def test_clover_since_mode_refuses(bound, monkeypatch):
    import typer

    from crude_clover import cli_orders

    monkeypatch.setattr(cli_orders, "_client",
                        lambda: pytest.fail("refused --since built a client"))
    with pytest.raises(typer.Exit):
        cli_orders.list_(from_=None, to=None, tz="Australia/Brisbane",
                         output="/dev/null", since=123, compare=False)


# ----------------------------------------------------------------------
# Rezdy: bookings bounded by maxDateCreated; dateUpdated flags mutation
# ----------------------------------------------------------------------


BOOKINGS = [
    # Future tour, booked before the cutoff: stays visible (knowledge time).
    {"orderNumber": "R1", "status": "CONFIRMED", "dateCreated": BEFORE,
     "dateUpdated": BEFORE, "items": [{"startTimeLocal": "2026-09-01 10:00:00"}]},
    # Booked before, cancelled after: current state, flagged.
    {"orderNumber": "R2", "status": "CANCELLED", "dateCreated": BEFORE,
     "dateUpdated": AFTER, "items": []},
    # Created after the cutoff: did not exist in the bounded world.
    {"orderNumber": "R3", "status": "CONFIRMED", "dateCreated": AFTER,
     "dateUpdated": AFTER, "items": []},
]


def _stub_rezdy_cli(monkeypatch, bookings, seen):
    from types import SimpleNamespace

    from crude_rezdy import cli as rcli

    def list_bookings(limit=20, offset=0, **kwargs):
        seen.update(kwargs)
        return list(bookings)

    stub = SimpleNamespace(list_bookings=list_bookings,
                           paginate=lambda limit=100, **kw: list_bookings(**kw))
    monkeypatch.setattr(rcli, "read_config",
                        lambda p: {"rezdy": {"api_key": "k", "timezone": "Australia/Brisbane"}})
    monkeypatch.setattr(rcli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(rcli, "_make_client", lambda config: stub)
    return rcli


def test_rezdy_booking_list_clamps_created_and_flags_updated(bound, monkeypatch, capsys):
    import json

    seen = {}
    rcli = _stub_rezdy_cli(monkeypatch, BOOKINGS, seen)
    rcli.list_bookings(status=None, search=None, product=None, from_=None, to=None,
                       created_from=None, created_to=None, updated_from=None,
                       updated_to=None, limit=20, offset=0, fetch_all=False,
                       output_json=True)
    assert seen["max_date_created"] == BOUND_Z                  # server bound injected
    out = json.loads(capsys.readouterr().out)
    assert [b["orderNumber"] for b in out] == ["R1", "R2"]      # created-after dropped
    assert asof.MARKER_KEY not in out[0]                        # future tour, pre-cutoff booking
    assert out[1][asof.MARKER_KEY] == asof.MUTATED              # cancelled post-cutoff


def test_rezdy_cancellations_exclude_post_cutoff_cancellations(bound, monkeypatch, capsys):
    import json

    seen = {}
    rcli = _stub_rezdy_cli(monkeypatch, BOOKINGS, seen)
    rcli.list_cancellations(from_=None, to=None, limit=100, fetch_all=False,
                            output_json=True)
    out = json.loads(capsys.readouterr().out)
    # R2's cancellation happened after the cutoff: at the bound it was not yet
    # a cancellation. R3 was created after the cutoff. Neither may appear.
    assert [b["orderNumber"] for b in out] == ["R1"]


# ----------------------------------------------------------------------
# Deputy: `Created le` injected into every QUERY; audit-field post-filter
# ----------------------------------------------------------------------


def test_deputy_query_injects_created_clause(bound, monkeypatch):
    from crude_deputy.client import DeputyClient

    client = DeputyClient("t", "install", "au")
    seen = {}

    def fake(method, url, **kw):
        seen.update(body=kw.get("json"))
        return _FakeResp(body=[])

    monkeypatch.setattr(client.session, "request", fake)
    client.query_resource("Roster", search={"f1": {"field": "Date", "type": "ge",
                                                   "data": "2026-09-01"}})
    clause = seen["body"]["search"]["_worldAsOf"]
    assert clause["field"] == "Created" and clause["type"] == "le"
    assert clause["data"].startswith("2026-07-12T17:07:00")
    assert seen["body"]["search"]["f1"]["field"] == "Date"      # user clause survives


def test_deputy_query_unbound_sends_no_clause(monkeypatch):
    monkeypatch.delenv(asof.ENV, raising=False)
    from crude_deputy.client import DeputyClient

    client = DeputyClient("t", "install", "au")
    seen = {}

    def fake(method, url, **kw):
        seen.update(body=kw.get("json"))
        return _FakeResp(body=[])

    monkeypatch.setattr(client.session, "request", fake)
    client.query_resource("Roster")
    assert "search" not in seen["body"]


def test_deputy_curated_list_drops_and_flags(bound, monkeypatch, capsys):
    import json

    from crude_deputy import cli as dcli

    rows = [
        # Rostered for next week, entered before the cutoff: visible, unflagged.
        {"Id": 1, "Date": "2026-09-01", "Created": BEFORE, "Modified": BEFORE},
        # Entered before, edited after: current state, flagged.
        {"Id": 2, "Date": "2026-09-01", "Created": BEFORE, "Modified": AFTER},
        # Entered after the cutoff (leaked past the server clause): dropped.
        {"Id": 3, "Date": "2026-06-01", "Created": AFTER, "Modified": AFTER},
    ]
    from types import SimpleNamespace

    stub = SimpleNamespace(query_resource=lambda obj, **kw: list(rows),
                           paginate_query=lambda obj, **kw: list(rows))
    monkeypatch.setattr(dcli, "_make_client", lambda config: stub)
    monkeypatch.setattr(dcli, "read_config", lambda p: {})
    monkeypatch.setattr(dcli, "find_config", lambda: "config.toml")
    dcli._curated_list("Roster", None, None, False, 100, None, True, "rosters")
    out = json.loads(capsys.readouterr().out)
    assert [r["Id"] for r in out] == [1, 2]
    assert asof.MARKER_KEY not in out[0]
    assert out[1][asof.MARKER_KEY] == asof.MUTATED


# ----------------------------------------------------------------------
# Skål: create_date <= bound injected into the Odoo domain; write_date flags
# ----------------------------------------------------------------------


def _skal_client():
    from crude_skal.client import SkalClient

    return SkalClient("session-id")


def test_skal_domain_gains_create_date_clause_and_audit_fields(bound, monkeypatch):
    client = _skal_client()
    seen = {}

    def fake_call_kw(model, method, args, kwargs):
        seen.update(model=model, domain=args[0], fields=kwargs["fields"])
        return [
            {"id": 1, "name": "Old", "create_date": "2026-07-01 00:00:00",
             "write_date": "2026-07-01 00:00:00"},
            {"id": 2, "name": "Edited", "create_date": "2026-07-01 00:00:00",
             "write_date": "2026-07-13 00:00:00"},
        ]

    monkeypatch.setattr(client, "_call_kw", fake_call_kw)
    records = client.list_members()
    assert ["create_date", "<=", "2026-07-12 07:07:00"] in seen["domain"]
    assert "create_date" in seen["fields"] and "write_date" in seen["fields"]
    assert asof.MARKER_KEY not in records[0]
    assert records[1][asof.MARKER_KEY] == asof.MUTATED


def test_skal_unbound_leaves_domain_and_fields_alone(monkeypatch):
    monkeypatch.delenv(asof.ENV, raising=False)
    client = _skal_client()
    seen = {}

    def fake_call_kw(model, method, args, kwargs):
        seen.update(domain=args[0], fields=kwargs["fields"])
        return []

    monkeypatch.setattr(client, "_call_kw", fake_call_kw)
    client.list_members()
    assert not any(c[0] == "create_date" for c in seen["domain"] if isinstance(c, (list, tuple)))
    assert "write_date" not in seen["fields"]


# ----------------------------------------------------------------------
# Xero: UpdatedDateUTC where-clause; journals exact; reports clamped
# ----------------------------------------------------------------------


def _xero_session():
    from crude_xero.client import XeroSession

    return XeroSession("acct", "cid", "secret",
                       {"access_token": "T", "refresh_token": "R",
                        "expires_at": time.time() + 9999})


def _xero_accounting(monkeypatch, body, seen):
    from crude_xero.accounting import AccountingAPI

    sess = _xero_session()

    def fake(method, url, **kw):
        seen.update(url=url, params=kw.get("params"))
        return _FakeResp(body=body)

    monkeypatch.setattr(sess.session, "request", fake)
    return AccountingAPI(sess)


def test_xero_list_injects_updated_where_and_composes_with_user_where(bound, monkeypatch):
    seen = {}
    api = _xero_accounting(monkeypatch, {"Invoices": []}, seen)
    api.list_invoices()
    assert seen["params"]["where"] == "UpdatedDateUTC <= DateTime(2026,7,12,7,7,0)"
    api.list_invoices(where='Status=="AUTHORISED"')
    assert seen["params"]["where"] == ('(Status=="AUTHORISED") AND '
                                       "UpdatedDateUTC <= DateTime(2026,7,12,7,7,0)")


def test_xero_list_drops_leaked_post_cutoff_rows(bound, monkeypatch):
    # The conservative rule: touched-after-cutoff rows are excluded, not flagged.
    rows = [
        {"InvoiceID": "a", "UpdatedDateUTC": "/Date(1751328000000+0000)/"},   # 2025
        {"InvoiceID": "b", "UpdatedDateUTC": "/Date(1789999999000+0000)/"},   # post-cutoff
    ]
    api = _xero_accounting(monkeypatch, {"Invoices": rows}, {})
    items = api.list_invoices()
    assert [r["InvoiceID"] for r in items] == ["a"]


def test_xero_stampless_collection_is_current_state_flagged(bound, monkeypatch):
    api = _xero_accounting(monkeypatch, {"Currencies": [{"Code": "AUD"}]}, {})
    items = api.list_currencies()
    assert items[0][asof.MARKER_KEY] == asof.CURRENT_STATE


def test_xero_get_refuses_record_touched_after_cutoff(bound, monkeypatch):
    body = {"Invoices": [{"InvoiceID": "b",
                          "UpdatedDateUTC": "/Date(1789999999000+0000)/"}]}
    api = _xero_accounting(monkeypatch, body, {})
    with pytest.raises(asof.WorldAsOfError):
        api.get_invoice("b")


def test_xero_journals_post_filter_created_exactly(bound, monkeypatch):
    rows = [
        {"JournalNumber": 1, "CreatedDateUTC": "/Date(1751328000000+0000)/"},
        {"JournalNumber": 2, "CreatedDateUTC": "/Date(1789999999000+0000)/"},
    ]
    api = _xero_accounting(monkeypatch, {"Journals": rows}, {})
    items = api.list_journals()
    assert [j["JournalNumber"] for j in items] == [1]


def test_xero_report_params_clamped_and_injected(bound, monkeypatch, capsys):
    seen = {}
    api = _xero_accounting(monkeypatch, {"Reports": []}, seen)
    api.get_report("BalanceSheet", {"date": "2026-08-01"})
    assert seen["params"]["date"] == "2026-07-12"              # clamped to the cutoff's date
    api.get_report("BalanceSheet", None)
    assert seen["params"]["date"] == "2026-07-12"              # injected, never defaults to today
    api.get_report("ProfitAndLoss", {"fromDate": "2026-06-01", "toDate": "2026-06-30"})
    assert seen["params"]["toDate"] == "2026-06-30"            # inside the world: untouched
    assert "computed from today's ledger" in capsys.readouterr().err


def test_xero_report_from_date_after_cutoff_refuses(bound, monkeypatch):
    import typer

    api = _xero_accounting(monkeypatch, {"Reports": []}, {})
    with pytest.raises(typer.Exit):
        api.get_report("ProfitAndLoss", {"fromDate": "2026-08-01"})


def test_xero_payroll_list_drops_touched_after_cutoff(bound, monkeypatch):
    from crude_xero.payroll import PayrollAU

    sess = _xero_session()
    rows = [
        {"EmployeeID": "a", "UpdatedDateUTC": "/Date(1751328000000+0000)/"},
        {"EmployeeID": "b", "UpdatedDateUTC": "/Date(1789999999000+0000)/"},
    ]
    monkeypatch.setattr(sess.session, "request",
                        lambda method, url, **kw: _FakeResp(body={"Employees": rows}))
    items = PayrollAU(sess).list_employees()
    assert [r["EmployeeID"] for r in items] == ["a"]


# ----------------------------------------------------------------------
# Facebook: until server param, reverse-chron belt filter, refusals
# ----------------------------------------------------------------------


def _fb_session_stub(posts, seen):
    from types import SimpleNamespace

    def iter_edge(path, params=None, token=None, max_items=None):
        seen.update(path=path, params=dict(params or {}), max_items=max_items)
        rows = iter(posts)
        count = 0
        for row in rows:
            yield row
            count += 1
            if max_items and count >= max_items:
                return

    return SimpleNamespace(page_id="P1", iter_edge=iter_edge,
                           get=lambda path, params=None: {})


def test_facebook_post_list_until_and_created_time_belt(bound, monkeypatch, capsys):
    import json

    from crude_facebook import cli_resources as fbr

    posts = [
        {"id": "p3", "created_time": AFTER},    # newest first: post-cutoff, dropped
        {"id": "p2", "created_time": BEFORE},
        {"id": "p1", "created_time": "2026-06-01T00:00:00+0000"},
    ]
    seen = {}
    monkeypatch.setattr(fbr, "_session", lambda: _fb_session_stub(posts, seen))
    fbr.post_list(scheduled=False, limit=2, output_json=True)
    assert seen["params"]["until"] == str(asof.bound_s())      # server filter sent
    out = json.loads(capsys.readouterr().out)
    assert [p["id"] for p in out] == ["p2", "p1"]              # belt keeps walking past drops


def test_facebook_scheduled_posts_refuse(bound, monkeypatch):
    import typer

    from crude_facebook import cli_resources as fbr

    monkeypatch.setattr(fbr, "_session",
                        lambda: pytest.fail("refused read built a session"))
    with pytest.raises(typer.Exit):
        fbr.post_list(scheduled=True, limit=25, output_json=False)


def test_facebook_insights_refuse(bound, monkeypatch):
    import typer

    from crude_facebook import cli_resources as fbr

    monkeypatch.setattr(fbr, "_session",
                        lambda: pytest.fail("refused read built a session"))
    with pytest.raises(typer.Exit):
        fbr.post_insights(post_id="p1", metric="post_clicks", output_json=False)
    with pytest.raises(typer.Exit):
        fbr.page_insights(metric="page_follows", period="day", output_json=False)


def test_facebook_comments_fetch_then_drop(bound, monkeypatch, capsys):
    import json

    from crude_facebook import cli_resources as fbr

    comments = [
        {"id": "c1", "created_time": BEFORE},
        {"id": "c2", "created_time": AFTER},
    ]
    monkeypatch.setattr(fbr, "_session", lambda: _fb_session_stub(comments, {}))
    fbr.comment_list(post_id="p1", output_json=True)
    out = json.loads(capsys.readouterr().out)
    assert [c["id"] for c in out] == ["c1"]


def test_facebook_page_get_is_current_state(bound, monkeypatch, capsys):
    import json

    from types import SimpleNamespace

    from crude_facebook import cli_resources as fbr

    stub = SimpleNamespace(page_id="P1",
                           get=lambda path, params=None: {"id": "P1", "followers_count": 9})
    monkeypatch.setattr(fbr, "_session", lambda: stub)
    fbr.page_get(output_json=True)
    out = json.loads(capsys.readouterr().out)
    assert out[asof.MARKER_KEY] == asof.CURRENT_STATE


# ----------------------------------------------------------------------
# Rezdy: availability refusal, vouchers by issueDate, catalog current-state
# ----------------------------------------------------------------------


def test_rezdy_availability_past_cutoff_refuses(bound, monkeypatch):
    import typer

    from crude_rezdy import cli as rcli

    monkeypatch.setattr(rcli, "read_config",
                        lambda p: {"rezdy": {"api_key": "k", "timezone": "Australia/Brisbane"}})
    monkeypatch.setattr(rcli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(rcli, "_client", lambda: pytest.fail("refused read built a client"))
    with pytest.raises(typer.Exit):
        rcli.list_availability(product="P1", from_="2026-07-20 00:00:00",
                               to="2026-07-25 23:59:59", min_availability=None,
                               limit=100, output_json=False)


def test_rezdy_vouchers_drop_issued_after_cutoff(bound, monkeypatch, capsys):
    import json

    from types import SimpleNamespace

    from crude_rezdy import cli as rcli

    vouchers = [
        {"code": "V1", "issueDate": BEFORE},
        {"code": "V2", "issueDate": AFTER},
    ]
    stub = SimpleNamespace(list_vouchers=lambda search="", limit=100, offset=0: list(vouchers))
    monkeypatch.setattr(rcli, "_client", lambda: stub)
    rcli.list_vouchers(search=None, limit=100, offset=0, output_json=True)
    out = json.loads(capsys.readouterr().out)
    assert [v["code"] for v in out] == ["V1"]


# ----------------------------------------------------------------------
# Clover: payments post-filtered by createdTime; flatten respects the bound
# ----------------------------------------------------------------------


def test_clover_flatten_drops_post_cutoff_orders(bound, tmp_path, capsys):
    import json

    from crude_clover.flatten import flatten

    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"items": []}))
    orders = tmp_path / "orders.jsonl"
    orders.write_text("\n".join(json.dumps(o) for o in [
        {"id": "o1", "createdTime": BOUND_MS - 1000,
         "lineItems": {"elements": [{"name": "Flat White", "price": 500}]}},
        {"id": "o2", "createdTime": BOUND_MS + 1000,
         "lineItems": {"elements": [{"name": "Future Latte", "price": 500}]}},
    ]))
    out = tmp_path / "out.csv"
    written = flatten(str(orders), str(catalog), str(out), "Australia/Brisbane")
    assert written == 1
    text = out.read_text()
    assert "Flat White" in text and "Future Latte" not in text
    assert "1 order(s) created after cutoff dropped" in capsys.readouterr().err


# ----------------------------------------------------------------------
# Sonas: createdAt-family post-filters (all bounding is client-side by
# construction of DDP) and the bounded corpus export
# ----------------------------------------------------------------------

EJ_BEFORE = {"$date": BOUND_MS - 86_400_000}
EJ_AFTER = {"$date": BOUND_MS + 86_400_000}


def test_sonas_filter_drops_created_after_and_flags_stampless(bound, capsys):
    from crude_sonas.cli import _asof_filter

    docs = [
        {"_id": "m1", "createdAt": EJ_BEFORE},
        {"_id": "m2", "createdAt": EJ_AFTER},
        {"_id": "m3"},                          # no stamp: kept, current-state
    ]
    kept = _asof_filter(docs, "message")
    assert [d["_id"] for d in kept] == ["m1", "m3"]
    assert asof.MARKER_KEY not in kept[0]
    assert kept[1][asof.MARKER_KEY] == asof.CURRENT_STATE
    assert "1 message(s) created after cutoff dropped" in capsys.readouterr().err


def test_sonas_event_created_prefers_enquiry_date(bound):
    from crude_sonas.cli import _event_created

    # The wedding date is the domain timeline and is never consulted.
    doc = {"date": EJ_AFTER, "enquiryData": {"date": EJ_BEFORE},
           "createdAt": EJ_AFTER}
    assert _event_created(doc) == EJ_BEFORE
    assert _event_created({"createdAt": EJ_BEFORE}) == EJ_BEFORE


def test_sonas_export_bundle_drops_post_cutoff_event(bound):
    from crude_sonas.cli import _asof_bundle

    bundle = {"event_id": "E1",
              "event": {"_id": "E1", "enquiryData": {"date": EJ_AFTER}},
              "messages": [], "transactions": [], "financial_records": [],
              "timelines": [], "service_bookings": []}
    assert _asof_bundle(bundle) is None


def test_sonas_export_bundle_filters_and_flags(bound):
    from crude_sonas.cli import _asof_bundle

    bundle = {
        "event_id": "E1",
        "event": {"_id": "E1", "enquiryData": {"date": EJ_BEFORE}, "status": 1},
        "messages": [{"_id": "m1", "createdAt": EJ_BEFORE},
                     {"_id": "m2", "createdAt": EJ_AFTER}],
        "transactions": [{"_id": "t1", "createdAt": EJ_AFTER}],
        "financial_records": [{"_id": "f1", "createdAt": EJ_BEFORE}],
        "timelines": [{"_id": "tl1"}],
        "service_bookings": [{"_id": "sb1"}],
    }
    out = _asof_bundle(bundle)
    assert [m["_id"] for m in out["messages"]] == ["m1"]        # post-cutoff message gone
    assert out["transactions"] == []
    assert [f["_id"] for f in out["financial_records"]] == ["f1"]
    assert out["event"][asof.MARKER_KEY] == asof.CURRENT_STATE  # mutable doc, flagged
    assert out["timelines"][0][asof.MARKER_KEY] == asof.CURRENT_STATE
    summary = out[asof.MARKER_KEY]
    assert summary["cutoff"] == BOUND
    assert summary["dropped"] == {"messages": 1, "transactions": 1,
                                  "financial_records": 0}


def test_sonas_export_bundle_falls_back_to_earliest_stamp(bound):
    from crude_sonas.cli import _asof_bundle

    # No enquiry date anywhere: the earliest createdAt in the bundle decides.
    late = {"event_id": "E2", "event": {"_id": "E2"},
            "messages": [{"_id": "m", "createdAt": EJ_AFTER}],
            "transactions": [], "financial_records": [],
            "timelines": [], "service_bookings": []}
    assert _asof_bundle(dict(late)) is None
    early = dict(late, messages=[{"_id": "m", "createdAt": EJ_BEFORE}])
    assert _asof_bundle(early) is not None


def test_sonas_export_bundle_unbound_is_identity(monkeypatch):
    monkeypatch.delenv(asof.ENV, raising=False)
    from crude_sonas.cli import _asof_bundle

    bundle = {"event": {"_id": "E1"}, "messages": [{"createdAt": EJ_AFTER}]}
    assert _asof_bundle(bundle) is bundle


# ----------------------------------------------------------------------
# ATDW: the weakest boundary — flag updatedOn, drop nothing
# ----------------------------------------------------------------------


def test_atdw_listings_flagged_never_dropped(bound, monkeypatch, capsys):
    import json

    from types import SimpleNamespace

    from crude_atdw import cli as acli

    listings = [
        {"id": "L1", "listingType": "tour", "slug": "a", "status": "ACTIVE",
         "updatedOn": BEFORE},
        {"id": "L2", "listingType": "tour", "slug": "b", "status": "ACTIVE",
         "updatedOn": AFTER},
    ]
    stub = SimpleNamespace(list_listings=lambda limit=20, skip=0: list(listings),
                           search_listings=lambda w, limit=20, skip=0: list(listings))
    monkeypatch.setattr(acli, "read_config", lambda p: {})
    monkeypatch.setattr(acli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(acli, "_make_client", lambda config: stub)
    acli.list_(scope="own", listing_type=None, city=None, state=None, status=None,
               name=None, limit=20, offset=0, output_json=True)
    out = json.loads(capsys.readouterr().out)
    assert [l["id"] for l in out] == ["L1", "L2"]              # nothing dropped
    assert asof.MARKER_KEY not in out[0]
    assert out[1][asof.MARKER_KEY] == asof.MUTATED             # touched-after flagged
