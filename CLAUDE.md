# crude

CRUD-style command-line clients for your own data on a handful of sites, some
without a usable public API and some over a documented one, under one
`crude-<site> <resource> <verb>` grammar. Each site ships as its own
`crude-<site>` binary; the `crude` launcher lists them and carries the shared
`--version` and `install-claude-command` flags. The binaries are declared in
`[project.scripts]` in `pyproject.toml`, which is the source of truth for the set.

Layout: `src/` holds `crude_common` and one `crude_<site>` package per binary.
The example config lives at `src/crude_common/config.example.toml` (shipped as
package data); `crude config-sample` prints it. Packaging lives in `debian/`
(`.deb`) and `crude.spec` (`.rpm`). The Homebrew formula lives in the dedicated
tap repo, overseers-desk/homebrew-od, at `Formula/crude.rb`; it points at crude's
release tarball and sha256. (crude is not published to PyPI: the name is taken.)

## Releasing

For cutting or recutting a release, follow [docs/RELEASING.md](docs/RELEASING.md).
"Release X.Y.Z" publishes the version already in the tree; it does not pick a new
version number.
