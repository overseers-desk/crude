# crude

crude is a lightweight command-line tool for CRUD access (create, read, update, delete) to your own data. Some sites lack a usable public API and are reached by reverse-engineering a login and calling internal endpoints; others ride a documented public API. Either way, every site is driven through one predictable command surface:

```
crude-<site> <resource> <verb> [id] [flags]
```

The clients ship as their own console binaries:

- `crude-atdw`: Australian Tourism Data Warehouse (ATDW) tourism listings (REST, OAuth bearer token).
- `crude-skal`: Skål Australia member portal (Odoo JSON-RPC, session cookie).
- `crude-rezdy`: Rezdy Supplier API for products, availability, and bookings (REST, API key).
- `crude-deputy`: Deputy workforce management: employees, rosters, timesheets, leave, and a generic resource sub-app for any Deputy object (REST, permanent API token).
- `crude-sonas`: Sonas wedding-venue software (Meteor DDP backend, session token).
- `crude-xero`: Xero accounting over the official OAuth2 APIs (REST, OAuth2 with automatic token refresh).
- `crude-airwallex`: Airwallex global payments and transactions, balances, payouts, and payment acceptance (REST, API-key bearer token).
- `crude-clover`: AP Clover POS orders, catalog, and Square-shape CSV export (REST, static bearer token).
- `crude-facebook`: Facebook Page posts, insights, and comments over the Graph API (REST, Page or System User token).
- `crude-mautic`: a self-hosted Mautic instance: website forms and their submissions, contacts, segments, campaigns (REST, HTTP basic auth).
- `crude-wise`: Wise Business balances, the balance statement, transfers, recipients, and the activity feed (REST, personal API token).

Running `crude` with no arguments lists these commands. `--version`, `--help`, and `install-claude-command` work on `crude` and on every site binary.

The tools are deliberately narrow. They authenticate, list, show, search, and edit your own records; they do not replicate every feature of the underlying web interfaces.

## Setup

After installing crude (see Install below), write the example config and fill in your credentials:

```
crude config-sample > ~/.config/crude/config.toml
```

Each site reads its own section (`[atdw]`, `[skal]`) from the one file. The CLIs look for `~/.config/crude/config.toml` first, then fall back to a `config.toml` in the repository root or the current directory for development. Config files are gitignored.

A site can carry more than one account. The bare `[site]` section is the default account; a `[site.<name>]` subtable is a named one, selected with `--account/-a` (or `$CRUDE_ACCOUNT`) before the resource. One example is a Rezdy venue in Australia and another in Spain, each with its own key and timezone. See the `crude config-sample` output.

### Install

Homebrew (macOS or Linux):

```
brew tap overseers-desk/od
brew install crude
```

Debian or Ubuntu: download the `.deb` from the releases page and install it with `sudo apt install ./crude_*_all.deb`.

From source with pip:

```
pip install -e .
```

Any of these put `crude` and the site binaries (`crude-atdw`, `crude-skal`, `crude-rezdy`, `crude-deputy`, `crude-sonas`, `crude-xero`, `crude-airwallex`, `crude-clover`, `crude-facebook`, `crude-mautic`, `crude-wise`) on your PATH. During development you can also run them without installing, from the `src/` directory, as `python3 -m crude_atdw <command>` (likewise `crude_skal`, `crude_rezdy`, `crude_deputy`, `crude_sonas`, `crude_xero`, `crude_airwallex`, `crude_clover`, `crude_facebook`, `crude_mautic`, `crude_wise`, and `crude_common.launcher` for the `crude` index).

### Claude Code command

The CLIs install a Claude Code command at `~/.claude/commands/crude.md` (covering every site) and keep it current automatically: every run rewrites the file when it is missing or differs from the bundled version. Run `crude-atdw install-claude-command` (or `crude-skal`, `crude-rezdy`, `crude-deputy`, `crude-sonas`, `crude-xero`, `crude-airwallex`, `crude-clover`, `crude-facebook`, `crude-mautic`, `crude-wise`) to write it explicitly. A same-named skill, if you keep one, takes precedence and the command is left alone.

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
crude-rezdy booking list --source-channel MYAGENT
crude-rezdy booking get R123456
```

`booking list --source-channel` takes the agent code from Rezdy's agents screen and `--reseller-reference` the agent's own booking number; `--product` repeats for several products. An order number goes to `booking get`: Rezdy has deprecated matching order numbers and agent codes through `--search`.

For a single day's bookings, give that day as both bounds: `crude-rezdy booking list --from 2026-05-25 --to 2026-05-25`. Rezdy's search takes UTC instants and would read the bare date as midnight UTC, so crude sends the first and last second of that day in the account's `timezone` instead; the same holds for `--created-from/--created-to`. A bound that carries a time is an instant, UTC unless it carries an offset. Availability times are local (`YYYY-MM-DD HH:mm:ss`).

`booking cancellations --from/--to` and `booking list --updated-from/--updated-to` filter on the cancellation/update instant, which Rezdy records in UTC, reading a typed date as the account's operational day in the same way. The lower bound is applied by Rezdy, so every page is already in range; the upper bound is checked on the pages fetched, so add `--all` when using it alone. A booking that was never updated carries no update instant and falls in no update window.

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

## Mautic usage (`crude-mautic`)

Mautic authenticates with a username and password sent as HTTP basic auth, set in the `[mautic]` section along with `base_url` (your instance, with no `/api` suffix); there is no login step. Mautic ships with both switches off, so turn on the API and HTTP basic auth under Configuration > API Settings before the first call. `crude-mautic status` confirms the credentials and names the signed-in user.

```
crude-mautic form list
crude-mautic form get website_en
crude-mautic form submissions website_en --group-by topic
crude-mautic form submissions website_en --where topic="Stallholder EOI"
crude-mautic contact list --search someone@example.com
crude-mautic segment list
crude-mautic email list
```

A form is addressed by its numeric id or by the alias its page markup carries. One Mautic form often serves several website pages, each marking its own traffic with a hidden field, so counting one page's submissions means filtering a shared form rather than reading a form of its own: `--group-by topic` counts every page's slice at once, and `--where topic=...` narrows to one. Submitted answers are HTML-unescaped before matching or grouping, so an answer stored two ways (`NDIS &amp; Disability` beside `NDIS & Disability`) counts once. `--field` picks which answer columns the table shows. Contacts take Mautic's own `--search` syntax, an email address or `segment:alias`. The client reads; it does not create or edit Mautic records.

## Wise usage (`crude-wise`)

Wise authenticates with a personal API token, set as `api_token` in the `[wise]` section; there is no login step. Create it in the Wise web UI under Your Account > Connect and manage apps > API tokens and paste it as issued: the token travels as an opaque bearer string, so the UUID tokens Wise issues today and the JWT tokens it is migrating to both work unchanged. Every profile-scoped read acts on the account's business profile; `profile_id` in the section pins one when the token reaches several. `crude-wise status` confirms the token and names the profile in use.

```
crude-wise status
crude-wise balance list
crude-wise transaction list --currency AUD --from 2026-09-01
crude-wise transfer list --from 2026-01-01 --limit 10
crude-wise recipient list
crude-wise activity list --from 2026-09-01
```

`transaction list` is the balance statement, the ledger behind one balance over a window of at most 469 days. Wise puts the statement behind strong customer authentication for profiles registered outside the US, Australia, New Zealand, Singapore, Canada and Malaysia: the call answers 403 with a one-time token, and crude resends it signed when `private_key` names an RSA private key whose public half is uploaded on the account's API tokens page (Manage public keys). Generate the pair with `openssl genrsa -out ~/.config/crude/wise-sca.pem 2048` and `openssl rsa -in ~/.config/crude/wise-sca.pem -pubout -out wise-sca.pub`, upload `wise-sca.pub`, and set `private_key` to the `.pem` path; signing runs through the `openssl` binary, so the key never enters the process. Without the key the command reports these steps; the other reads need no key. The client reads; it does not create transfers or recipients.

## Further reference

- `docs/manual.md`: full ATDW command reference with flag tables and filter syntax
- `docs/APIs.md`: reverse-engineered ATDW API reference
- `docs/skal-api.md`: Skål portal API reference and club IDs
- `docs/rezdy.md`: Rezdy command surface and API boundary
- `docs/sonas.md`: Sonas resource map and DDP protocol
- `docs/xero.md`: Xero command surface, auth, and tenant model
- `docs/airwallex.md`: Airwallex command surface, auth, and the verified API behaviour
