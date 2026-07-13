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
