# crude

CRUD-style command-line clients for your own data on a handful of sites, some
without a usable public API and some over a documented one, under one
`crude-<site> <resource> <verb>` grammar. The `crude` launcher lists the site
binaries and carries the shared `--version` and `install-claude-command` flags.

Any agent editing this software must first read [README.md](README.md): it
states the shared models (the site grammar, the multi-account model, the config
layout) that no single source file shows whole, and code or analysis written
without it re-derives those models wrongly.

The per-site layout rules that must hold are in [`INVARIANTS.md`](INVARIANTS.md);
a change that breaks one is a design change, the owner's to make.

@INVARIANTS.md

The example config lives at `src/crude_common/config.example.toml` (shipped as
package data); `crude config-sample` prints it. Packaging lives in `debian/`
(`.deb`) and `crude.spec` (`.rpm`). The Homebrew formula lives in the dedicated
tap repo, overseers-desk/homebrew-od, at `Formula/crude.rb`; it points at crude's
release tarball and sha256. (crude is not published to PyPI: the name is taken.)

## Releasing

For cutting or recutting a release, follow [docs/RELEASING.md](docs/RELEASING.md).
"Release X.Y.Z" publishes the version already in the tree; it does not pick a new
version number.
