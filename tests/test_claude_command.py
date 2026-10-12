"""The command file lands in the configuration tree the running session reads.

One machine carries several Claude Code configuration trees, and
``CLAUDE_CONFIG_DIR`` names the one in force. A refresh that always wrote to
``~/.claude`` left every other tree without a crude command, so a session
started against one of them had no way to reach Xero or Deputy.
"""

from pathlib import Path

from crude_common import claude_command


def test_configured_tree_wins_over_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert claude_command.command_file() == tmp_path / "commands" / "crude.md"


def test_home_is_the_fallback(monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    expected = Path.home() / ".claude" / "commands" / "crude.md"
    assert claude_command.command_file() == expected


def test_refresh_writes_into_the_configured_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    claude_command.refresh()
    assert (tmp_path / "commands" / "crude.md").read_text() == claude_command.COMMAND


def test_a_same_named_skill_supersedes_the_command(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "skills" / "crude").mkdir(parents=True)
    claude_command.refresh()
    assert not (tmp_path / "commands" / "crude.md").exists()


def test_an_unwritable_tree_is_skipped_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    commands = tmp_path / "commands"
    commands.mkdir()
    commands.chmod(0o500)
    try:
        claude_command.refresh()
    finally:
        commands.chmod(0o700)
    assert not (commands / "crude.md").exists()
