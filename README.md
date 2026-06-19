# crude

crude is a lightweight command-line tool for CRUD access (create, read, update, delete) to your own data. Some sites lack a usable public API and are reached by reverse-engineering a login and calling internal endpoints; others ride a documented public API. Either way, every site is driven through one predictable command surface:

```
crude-<site> <resource> <verb> [id] [flags]
```

Seven sites ship today, each as its own console binary:

- `crude-atdw`: Australian Tourism Data Warehouse (ATDW) tourism listings (REST, OAuth bearer token).
- `crude-skal`: Skål Australia member portal (Odoo JSON-RPC, session cookie).
- `crude-rezdy`: Rezdy Supplier API for products, availability, and bookings (REST, API key).
- `crude-deputy`: Deputy workforce management: employees, rosters, timesheets, leave, and a generic resource sub-app for any Deputy object (REST, permanent API token).
- `crude-sonas`: Sonas wedding-venue software (Meteor DDP backend, session token).
- `crude-xero`: Xero accounting over the official OAuth2 APIs (REST, OAuth2 with automatic token refresh).
- `crude-airwallex`: Airwallex global payments and transactions, balances, payouts, and payment acceptance (REST, API-key bearer token).

Running `crude` with no arguments lists these commands. `--version`, `--help`, and `install-claude-command` work on `crude` and on every site binary.

The tools are deliberately narrow. They authenticate, list, show, search, and edit your own records; they do not replicate every feature of the underlying web interfaces.

## Setup

Copy the example config and fill in your credentials:

```
cp config.example.toml ~/.config/crude/config.toml
```

Each site reads its own section (`[atdw]`, `[skal]`) from the one file. The CLIs look for `~/.config/crude/config.toml` first, then fall back to a `config.toml` in the repository root or the current directory for development. Config files are gitignored.

A site can carry more than one account. The bare `[site]` section is the default account; a `[site.<name>]` subtable is a named one, selected with `--account/-a` (or `$CRUDE_ACCOUNT`) before the resource. One example is a Rezdy venue in Australia and another in Spain, each with its own key and timezone. See `config.example.toml`.

### Install

Homebrew (macOS or Linux):

```
brew tap SmartLayer/crude https://github.com/SmartLayer/crude
brew trust --formula SmartLayer/crude/crude
brew install crude
```

Debian or Ubuntu: download the `.deb` from the releases page and install it with `sudo apt install ./crude_*_all.deb`.

From source with pip:

```
pip install -e .
```

Any of these put `crude` and the seven site binaries (`crude-atdw`, `crude-skal`, `crude-rezdy`, `crude-deputy`, `crude-sonas`, `crude-xero`, `crude-airwallex`) on your PATH. During development you can also run them without installing, from the `src/` directory, as `python3 -m crude_atdw <command>` (likewise `crude_skal`, `crude_rezdy`, `crude_deputy`, `crude_sonas`, `crude_xero`, `crude_airwallex`, and `crude_common.launcher` for the `crude` index).

### Claude Code command

The CLIs install a Claude Code command at `~/.claude/commands/crude.md` (covering all seven sites) and keep it current automatically: every run rewrites the file when it is missing or differs from the bundled version. Run `crude-atdw install-claude-command` (or `crude-skal`, `crude-rezdy`, `crude-deputy`, `crude-sonas`, `crude-xero`, `crude-airwallex`) to write it explicitly. A same-named skill, if you keep one, takes precedence and the command is left alone.

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
crude-skal benefit list
crude-skal benefit get 178
```

With no filters, `member list` returns the current Australian member roster. Filter flags (`--name`, `--city`, `--club`, `--email`, `--state`) narrow the search. `benefit list` shows the global Skål International benefits register (worldwide offers); Australian clubs' own member discounts are published on a website page, not in this model.

## Rezdy usage (`crude-rezdy`)

Rezdy authenticates with a Supplier API key, set in the `[rezdy]` section of the config; there is no login step. The section also requires a `timezone` (IANA name, e.g. `Australia/Brisbane`): rezdy reads every typed date as that account's operational day, so any rezdy command errors if the field is missing.

```
crude-rezdy product list --search "kayak" --limit 10
crude-rezdy product get P12345
crude-rezdy availability list --product P12345 --from "2026-05-25 00:00:00" --to "2026-05-31 23:59:59"
crude-rezdy booking list --status CONFIRMED --product P12345
crude-rezdy booking get R123456
```

For a single day's bookings, set the tour-time bounds to that day: `crude-rezdy booking list --from 2026-05-25T00:00:00Z --to 2026-05-25T23:59:59Z`. Availability times are local (`YYYY-MM-DD HH:mm:ss`); booking times are ISO 8601.

`booking cancellations --from/--to` and `booking list --updated-from/--updated-to` filter client-side against the cancellation/update instant, which Rezdy records in UTC. crude reads the typed date as the account's operational day (the `timezone` above) and converts to UTC for the comparison, so a date typed at the day boundary is not filed a day off.

### JSON output

All read commands accept `--json` to emit raw JSON instead of a formatted table:

```
crude-atdw listing list --json
crude-skal member get 184914 --json
crude-rezdy booking list --json
```

## Airwallex usage (`crude-airwallex`)

Airwallex authenticates with a `client_id` and `api_key` (both generated under Developer > API keys in the Airwallex console), set in the `[airwallex]` section; there is no separate login step, though `crude-airwallex login` confirms the credentials and reports the token's expiry. All timestamps print in your computer's local timezone, and `--from`/`--to` filters are read as local dates.

```
crude-airwallex balance current
crude-airwallex transaction list --from 2026-05-01 --to 2026-06-17 --limit 20
crude-airwallex transaction get <id>
crude-airwallex beneficiary list
crude-airwallex conversion list
crude-airwallex pa payment-intent list
```

The command groups are the treasury reads (`account`, `balance`, `transaction`), Payouts (`beneficiary`, `transfer`, `fx-rate`, `conversion`), and Payments Acceptance (the `pa` group). Reads accept `--json`. Verbs that move money (`transfer create`, `conversion create`, `pa payment-intent create`, `pa refund create`, and the like) prompt for confirmation unless you pass `--yes`. Some products need separate enablement on your Airwallex account; a call to one that is not enabled reports that plainly rather than failing obscurely. The full command surface and the verified API specifics are in `docs/airwallex.md`.

## Further reference

- `docs/manual.md`: full ATDW command reference with flag tables and filter syntax
- `docs/APIs.md`: reverse-engineered ATDW API reference
- `docs/skal-api.md`: Skål portal API reference and club IDs
- `docs/rezdy.md`: Rezdy command surface and API boundary
- `docs/sonas.md`: Sonas resource map and DDP protocol
- `docs/xero.md`: Xero command surface, auth, and tenant model
- `docs/airwallex.md`: Airwallex command surface, auth, and the verified API behaviour
