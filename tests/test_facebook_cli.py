"""crude_facebook CLI structure tests (no network).

Pins the flattened command surface: the resource groups are mounted directly on
the root (no platform sub-level), and status is a root command.
"""

from crude_facebook import cli_resources as r


def _cmds(typer_app):
    return {c.name for c in typer_app.registered_commands}


def _groups(typer_app):
    return {g.name for g in typer_app.registered_groups}


def test_resource_command_surface():
    assert _cmds(r.post) == {"list", "get", "insights", "create", "edit", "delete"}
    assert _cmds(r.comment) == {"list", "reply", "hide", "unhide", "delete"}
    assert _cmds(r.page) == {"get", "insights"}


def test_root_is_flat_with_status():
    from crude_facebook.cli import app

    assert {"post", "comment", "page"} <= _groups(app)
    assert "status" in _cmds(app)
    # No platform sub-level on the flattened grammar.
    assert "instagram" not in _groups(app)
    assert "facebook" not in _groups(app)
