"""Unit tests for `crude-rezdy export`: the files it writes, its manifest, and that
it only reads. The API is the in-memory fake in rezdy_fake.py; every record is
made up."""

import json
import stat
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from crude_rezdy import cli
from crude_rezdy.client import RezdyClient
from rezdy_fake import FakeRezdy, session

runner = CliRunner()

EVERY_FILE = {
    "products.json", "product-detail.json", "product-pickups.json", "company.json",
    "availability.json", "bookings.json", "customers.json", "extras.json",
    "pickup-lists.json", "pickup-list-detail.json", "categories.json", "category-products.json",
    "rates.json", "rate-detail.json", "resources.json", "resource-sessions.json",
    "vouchers.json", "manifest.json",
}


@pytest.fixture
def account(monkeypatch):
    """A small made-up account behind the CLI's client."""
    fake = FakeRezdy()
    fake.products = [
        {"productCode": "PA", "name": "Ride", "supplierAlias": "demo",
         "extras": [{"name": "Photo"}, {"name": "(odd) Blanket"}]},
        {"productCode": "PB", "name": "Lesson", "supplierAlias": "demo", "extras": []},
    ]
    fake.customers = [{"id": i, "firstName": "Test", "lastName": f"Person{i}"} for i in range(150)]
    fake.vouchers = [{"code": "V1", "status": "VALID"}]
    fake.categories = [{"id": 1, "name": "Rides", "visible": True}]
    fake.category_products["1"] = [fake.products[0]]
    fake.resources = [{"id": 5, "name": "Horse"}]
    fake.sessions_by_resource["5"] = [session(i, f"2026-11-0{i} 10:00:00") for i in range(1, 4)]
    fake.availability["PA"] = [session(11, "2026-11-02 09:00:00"), session(12, "2026-12-30 09:00:00")]
    fake.bookings = [{"orderNumber": f"R{i}", "status": "CONFIRMED"} for i in range(130)]
    fake.extras = [{"id": 1, "name": "Photo", "price": 5}, {"id": 2, "name": "(odd) Blanket", "price": 3}]
    fake.pickup_lists = [{"id": 8, "name": "Town"}]
    fake.rates = [{"rateId": 9, "name": "Adult"}]
    fake.companies = {"demo": {"name": "Demo Farm"}}
    # The prefix walk cannot reach "(odd) Blanket"; fill the first 100 with others.
    fake.extras += [{"id": 100 + i, "name": f"Zextra {i}", "price": 1} for i in range(110)]

    client = RezdyClient("KEY")
    monkeypatch.setattr(client.session, "request", fake.request)
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    monkeypatch.setattr(cli, "find_config", lambda: "config.toml")
    monkeypatch.setattr(
        cli, "read_config",
        lambda _p: {"rezdy": {"api_key": "KEY", "timezone": "Australia/Brisbane"}},
    )
    monkeypatch.setattr(cli, "_make_client", lambda _c: client)
    return fake


def load(directory, name):
    return json.loads((directory / name).read_text())


def test_export_writes_every_resource_and_a_manifest(account, tmp_path):
    out = tmp_path / "dump"
    result = runner.invoke(cli.app, ["export", str(out), "--from", "2026-11-01", "--to", "2026-12-31"])
    assert result.exit_code == 0, result.output
    assert {p.name for p in out.iterdir()} == EVERY_FILE

    assert [p["productCode"] for p in load(out, "products.json")] == ["PA", "PB"]
    assert set(load(out, "product-detail.json")) == {"PA", "PB"}
    assert len(load(out, "customers.json")) == 150
    assert len(load(out, "bookings.json")) == 130
    assert [s["id"] for s in load(out, "availability.json")["PA"]] == [11, 12]
    assert [s["id"] for s in load(out, "resource-sessions.json")["5"]] == [1, 2, 3]
    assert load(out, "category-products.json")["1"][0]["productCode"] == "PA"
    assert load(out, "company.json")["demo"]["name"] == "Demo Farm"
    # An extra the prefix walk cannot reach, found because a product carries it.
    assert "(odd) Blanket" in {e["name"] for e in load(out, "extras.json")}

    manifest = load(out, "manifest.json")
    assert manifest["complete"] is True
    assert manifest["window"]["from"] == "2026-11-01 00:00:00"
    assert manifest["window"]["to"] == "2026-12-31 23:59:59"
    assert manifest["timezone"] == "Australia/Brisbane"
    assert manifest["files"]["customers.json"] == {"records": 150}
    assert manifest["files"]["availability.json"] == {"keys": 2, "records": 2}
    assert manifest["extras_found_by_name"] == 1
    assert manifest["errors"] == [] and manifest["gaps"] == []


def test_export_only_reads(account, tmp_path):
    runner.invoke(cli.app, ["export", str(tmp_path / "d"), "--from", "2026-11-01", "--to", "2026-12-31"])
    assert account.calls and account.methods() == {"GET"}


def test_export_files_are_private(account, tmp_path):
    out = tmp_path / "dump"
    runner.invoke(cli.app, ["export", str(out), "--only", "customers"])
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert stat.S_IMODE((out / "customers.json").stat().st_mode) == 0o600


def test_export_only_a_part_needs_no_window(account, tmp_path):
    out = tmp_path / "dump"
    result = runner.invoke(cli.app, ["export", str(out), "--only", "customers", "--only", "vouchers"])
    assert result.exit_code == 0, result.output
    assert {p.name for p in out.iterdir()} == {"customers.json", "vouchers.json", "manifest.json"}
    assert load(out, "manifest.json")["stages"] == ["customers", "vouchers"]


def test_export_asks_for_the_window_before_calling_anything(account, tmp_path):
    result = runner.invoke(cli.app, ["export", str(tmp_path / "dump")])
    assert result.exit_code == 1
    assert "--from and --to" in result.output
    assert account.calls == []


def test_export_refuses_an_unknown_part(account, tmp_path):
    result = runner.invoke(cli.app, ["export", str(tmp_path / "dump"), "--only", "bogus"])
    assert result.exit_code == 1
    assert account.calls == []


def test_export_keeps_going_past_a_failed_record_and_says_so(account, tmp_path, monkeypatch):
    real = account.request

    def flaky(method, url, params=None, json=None):
        if url.endswith("/v1/products/PB"):
            body = {"requestStatus": {"success": False, "error": {"errorMessage": "gone"}}}
            return SimpleNamespace(ok=False, status_code=404, json=lambda: body, headers={})
        return real(method, url, params, json)

    cli._make_client(None).session.request = flaky
    out = tmp_path / "dump"
    result = runner.invoke(cli.app, ["export", str(out), "--from", "2026-11-01", "--to", "2026-12-31"])
    assert result.exit_code == 1
    manifest = load(out, "manifest.json")
    assert manifest["complete"] is False
    assert [e["what"] for e in manifest["errors"]] == ["product PB"]
    assert "gone" in manifest["errors"][0]["error"]
    # PA's detail and every other file are there.
    assert set(load(out, "product-detail.json")) == {"PA"}
    assert len(load(out, "customers.json")) == 150


def test_export_reports_a_window_of_sessions_it_could_not_cut(account, tmp_path):
    day = date(2026, 11, 10)
    account.sessions_by_resource["5"] = [
        session(i, (datetime.combine(day, datetime.min.time()) + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"))
        for i in range(1, 121)]
    out = tmp_path / "dump"
    result = runner.invoke(cli.app, ["export", str(out), "--only", "resources",
                                     "--from", "2026-11-01", "--to", "2026-12-31"])
    assert result.exit_code == 1
    manifest = load(out, "manifest.json")
    assert manifest["complete"] is False
    assert manifest["gaps"] and manifest["gaps"][0].startswith("resource 5:")
