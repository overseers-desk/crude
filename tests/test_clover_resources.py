"""crude_clover resource registry, factory, and scope-probe helpers (no network).

Pins the registry-to-CLI mapping (every resource gets list/get; writable ones
also get create/update/delete; singletons get only get), the generic
auto-column selection, and the write-scope classifier.
"""

from crude_clover.cli_resources import _auto_columns, _resource
from crude_clover.cli_status import classify_write
from crude_clover.resources import BY_NAME, REGISTRY, ResourceAPI


def _command_names(spec):
    return {c.name for c in _resource(spec).registered_commands}


def test_registry_is_well_formed():
    names = [s.name for s in REGISTRY]
    assert len(names) == len(set(names)), "resource names must be unique"
    assert BY_NAME["items"].segment == "items"
    assert BY_NAME["modifier-groups"].segment == "modifier_groups"
    # merchant info is a get-only singleton with an empty segment.
    assert BY_NAME["merchant"].singleton and BY_NAME["merchant"].segment == ""


def test_writable_resource_gets_full_crud():
    assert _command_names(BY_NAME["items"]) == {"list", "get", "create", "update", "delete"}


def test_readonly_resource_is_list_get_only():
    assert _command_names(BY_NAME["payments"]) == {"list", "get"}
    assert "create" not in _command_names(BY_NAME["devices"])


def test_singleton_is_get_only():
    assert _command_names(BY_NAME["merchant"]) == {"get"}


def test_auto_columns_picks_id_then_scalars():
    record = {"id": "X", "name": "Coffee", "price": 500,
              "categories": {"elements": []}, "tags": [1, 2], "code": "C1"}
    cols = _auto_columns(record)
    assert cols[0] == "id"
    assert "name" in cols and "price" in cols and "code" in cols
    assert "categories" not in cols and "tags" not in cols  # nested dropped


def test_classify_write():
    assert classify_write(403) == "blocked"
    assert classify_write(401) == "blocked"
    assert classify_write(404) == "enabled"   # authorised, record absent
    assert classify_write(400) == "enabled"   # authorised, bad body
    assert classify_write(201) == "enabled-unexpected"
    assert classify_write(500) == "undetermined"


def test_resource_api_path_construction():
    from types import SimpleNamespace

    api = ResourceAPI(SimpleNamespace(merchant_id="M123"))
    assert api._path("items") == "/v3/merchants/M123/items"
    assert api._path("items", "ABC") == "/v3/merchants/M123/items/ABC"
    assert api._path("") == "/v3/merchants/M123"  # merchant singleton
