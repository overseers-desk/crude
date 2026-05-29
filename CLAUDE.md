# crude

CRUD-style command-line clients for sites without a usable public API, under one
`crude-<site> <resource> <verb>` grammar. Three sites ship as separate binaries:
`crude-atdw`, `crude-skal`, `crude-rezdy`. The `crude` launcher lists them and
carries the shared `--version` and `install-claude-command` flags.

Layout: `src/` holds the four packages (`crude_common`, `crude_atdw`,
`crude_skal`, `crude_rezdy`). Packaging lives in `debian/` (`.deb`), `crude.spec`
(`.rpm`), and `formula/crude.rb` (Homebrew).

## Releasing

For cutting or recutting a release, follow [docs/RELEASING.md](docs/RELEASING.md).
"Release X.Y.Z" publishes the version already in the tree; it does not pick a new
version number.
