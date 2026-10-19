"""Every crude binary's root app keeps frame locals out of its tracebacks.

crude is run by AI agents, whose transcripts keep whatever a crash prints. Typer's
rich traceback shows each frame's local variables, by default in Typer 0.15 at
least, and a frame there holds the parsed config, every site's password
and token included. So each root app named in ``[project.scripts]`` must pass
``pretty_exceptions_show_locals=False`` itself. The check reads the ``Typer(...)``
call in the source rather than the app's attribute: under a Typer whose default is
False, the attribute would read False whether or not the call sets it.
"""

import ast
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]


def _source(module: str) -> Path:
    base = ROOT / "src" / Path(*module.split("."))
    path = base.with_suffix(".py")
    return path if path.exists() else base / "__init__.py"


def _typer_call(module: str, attr: str) -> ast.Call:
    """The call assigned to ``attr`` at the top level of ``module``."""
    for node in ast.parse(_source(module).read_text()).body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == attr for t in node.targets)
            and isinstance(node.value, ast.Call)
        ):
            return node.value
    raise AssertionError(f"{module} has no top-level {attr} = typer.Typer(...)")


def test_the_scripts_table_is_read():
    assert SCRIPTS


@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_root_app_passes_show_locals_false(script):
    module, attr = SCRIPTS[script].split(":")
    call = _typer_call(module, attr)
    assert ast.unparse(call.func) == "typer.Typer", f"{script}: {attr} is not a typer.Typer(...)"
    keywords = {k.arg: k.value for k in call.keywords}
    assert "pretty_exceptions_show_locals" in keywords, (
        f"{script}: {module}.{attr} = typer.Typer(...) must pass "
        "pretty_exceptions_show_locals=False"
    )
    value = keywords["pretty_exceptions_show_locals"]
    assert isinstance(value, ast.Constant) and value.value is False, (
        f"{script}: pretty_exceptions_show_locals must be the literal False"
    )
