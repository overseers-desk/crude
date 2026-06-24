"""crude_meta CLI structure tests (no network).

Pins the command surface: which verbs each platform group exposes, and that the
root wires the instagram/facebook/account groups and the status command.
"""

from crude_meta import cli_facebook as fb
from crude_meta import cli_instagram as ig
from crude_meta import cli_status as st


def _cmds(typer_app):
    return {c.name for c in typer_app.registered_commands}


def _groups(typer_app):
    return {g.name for g in typer_app.registered_groups}


def test_instagram_command_surface():
    assert _cmds(ig.media) == {"list", "get", "insights", "publish", "delete"}
    assert _cmds(ig.comment) == {"list", "reply", "hide", "unhide", "delete", "toggle"}
    assert _cmds(ig.account_app) == {"get", "insights"}
    assert _groups(ig.app) == {"media", "comment", "account"}


def test_facebook_command_surface():
    assert _cmds(fb.post) == {"list", "get", "insights", "create", "edit", "delete"}
    assert _cmds(fb.comment) == {"list", "reply", "hide", "unhide", "delete"}
    assert _cmds(fb.page) == {"get", "insights"}
    assert _groups(fb.app) == {"post", "comment", "page"}


def test_status_surface():
    assert _cmds(st.account_app) == {"show"}


def test_root_wires_platforms_and_status():
    from crude_meta.cli import app

    assert {"instagram", "facebook", "account"} <= _groups(app)
    assert "status" in _cmds(app)
