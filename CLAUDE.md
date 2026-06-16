# crude

CRUD-style command-line clients for sites without a usable public API, under one
`crude-<site> <resource> <verb>` grammar. Each site ships as its own
`crude-<site>` binary; the `crude` launcher lists them and carries the shared
`--version` and `install-claude-command` flags. The binaries are declared in
`[project.scripts]` in `pyproject.toml`, which is the source of truth for the set.

Layout: `src/` holds `crude_common` and one `crude_<site>` package per binary.
Packaging lives in `debian/` (`.deb`), `crude.spec` (`.rpm`), and
`formula/crude.rb` (Homebrew).

## Releasing

For cutting or recutting a release, follow [docs/RELEASING.md](docs/RELEASING.md).
"Release X.Y.Z" publishes the version already in the tree; it does not pick a new
version number.
