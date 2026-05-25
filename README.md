# crude

CRUD-Engine (crude) is a lightweight command-line tool for programmatic read-and-write access to one's own data on sites that lack a usable public API. Each site is reached by reverse-engineering its login and calling its internal endpoints, and every site is driven through one predictable command surface:

```
crude-<site> <resource> <verb> [id] [flags]
```

Three sites ship today, each as its own console binary:

- `crude-atdw`: Australian Tourism Data Warehouse (ATDW) tourism listings (REST, OAuth bearer token).
- `crude-skal`: Skål Australia member portal (Odoo JSON-RPC, session cookie).
- `crude-rezdy`: Rezdy Supplier API for products, availability, and bookings (REST, API key).

The tools are deliberately narrow. They authenticate, list, show, search, and edit your own records; they do not replicate every feature of the underlying web interfaces.

## Setup

Copy the example config and fill in your credentials:

```
cp config.example.toml ~/.config/crude/config.toml
```

Each site reads its own section (`[atdw]`, `[skal]`) from the one file. The CLIs look for `~/.config/crude/config.toml` first, then fall back to a `config.toml` in the repository root or the current directory for development. Config files are gitignored.

### Install

Homebrew (macOS or Linux):

```
brew tap SmartLayer/crude https://github.com/SmartLayer/crude
brew install crude
```

Debian or Ubuntu: download `crude_1.0.0_all.deb` from the releases page and install it with `sudo apt install ./crude_1.0.0_all.deb`.

From source with pip:

```
pip install -e .
```

Any of these put `crude-atdw`, `crude-skal`, and `crude-rezdy` on your PATH. During development you can also run them without installing, from the `src/` directory, as `python3 -m crude_atdw <command>` (likewise `crude_skal`, `crude_rezdy`).

### Claude Code skill

Run `crude-atdw install-claude-command` (or `crude-skal` / `crude-rezdy`) to install a single Claude Code skill at `~/.claude/skills/crude/SKILL.md` covering all three sites. The CLIs print a one-line reminder to stderr when that skill is missing or out of date, so an agent knows to (re)install it.

## Dependencies

Python 3.9+.

**Ubuntu/Debian:**

```
sudo apt-get install python3-typer python3-rich python3-requests python3-tomli python3-tomli-w
```

`python3-tomli-w` is in the `universe` repository; enable it with `sudo apt-get install software-properties-common && sudo add-apt-repository universe` if the package is not found.

**OS X**: these packages are not in Homebrew, so use pip:

```
pip3 install "typer[all]" requests tomli tomli-w
```

## ATDW usage (`crude-atdw`)

### Login

```
crude-atdw login
```

Reads credentials from config, authenticates via the ATDW OAuth2 flow, and caches the JWT token (valid about 7 hours). If it expires, any subsequent command re-authenticates automatically.

### List and search listings

```
crude-atdw listing list
crude-atdw listing list --scope all --type tour --limit 5
crude-atdw listing list --scope all --city "Gold Coast"
crude-atdw listing list --scope all --name "beach"
```

With no filters, `listing list` returns your organisation's own listings. Any filter flag (`--type`, `--city`, `--state`, `--status`, `--name`), or `--scope all`, switches to the all-visible search across every listing. `--limit`, `--offset`, and `--json` apply throughout.

### Show a single listing

```
crude-atdw listing get 6568273cc9320b7770116404
```

Displays key fields (name, type, status, description, dates) plus media count, services count, and tags.

### Update a listing field

```
crude-atdw listing update 6568273cc9320b7770116404 description "New description text"
```

Sends a PATCH with only the named field. Works for any string field in the data model (`description`, `shortDescription`, `name`, etc.).

### Submit for review

```
crude-atdw listing submit 69b14f64d5bb6b47750392c1
```

Submits a `DRAFT`/`DRAFTINPROG` listing for ATDW review.

## Skål usage (`crude-skal`)

```
crude-skal login
crude-skal member list
crude-skal member list --city "Gold Coast" --limit 5
crude-skal member get 184914
crude-skal club list
crude-skal event list
```

With no filters, `member list` returns the current Australian member roster. Filter flags (`--name`, `--city`, `--club`, `--email`, `--state`) narrow the search.

## Rezdy usage (`crude-rezdy`)

Rezdy authenticates with a Supplier API key, set in the `[rezdy]` section of the config; there is no login step.

```
crude-rezdy product list --search "kayak" --limit 10
crude-rezdy product get P12345
crude-rezdy availability list --product P12345 --from "2026-05-25 00:00:00" --to "2026-05-31 23:59:59"
crude-rezdy booking list --status CONFIRMED --product P12345
crude-rezdy booking get R123456
```

For a single day's bookings, set the tour-time bounds to that day: `crude-rezdy booking list --from 2026-05-25T00:00:00Z --to 2026-05-25T23:59:59Z`. Availability times are local (`YYYY-MM-DD HH:mm:ss`); booking times are ISO 8601.

### JSON output

All read commands accept `--json` to emit raw JSON instead of a formatted table:

```
crude-atdw listing list --json
crude-skal member get 184914 --json
crude-rezdy booking list --json
```

## Further reference

- `docs/manual.md`: full ATDW command reference with flag tables and filter syntax
- `docs/APIs.md`: reverse-engineered ATDW API reference
- `docs/skal-api.md`: Skål portal API reference and club IDs
