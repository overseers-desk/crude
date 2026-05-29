"""crude_common — shared helpers across the crude site CLIs."""

from importlib.metadata import PackageNotFoundError, version as _dist_version


def version() -> str:
    """The installed crude version, read from distribution metadata.

    Single-sourced from the package metadata (pyproject's ``version``); no string
    is re-stated here. Running from an uninstalled source tree has no metadata, so
    a development sentinel is returned instead of raising.
    """
    try:
        return _dist_version("crude")
    except PackageNotFoundError:
        return "0+unknown"
