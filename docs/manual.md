# crude-atdw manual

Command-line client for the ATDW (Australian Tourism Data Warehouse) REST API, part of the crude tool.

## Installation

Dependencies are declared in `pyproject.toml`: `typer[all]`, `requests`, `tomli_w` (plus `tomli` on Python < 3.11, which otherwise uses the stdlib `tomllib`). Rich is pulled in by `typer[all]` and used for table output.

Install the package so the console scripts land on your PATH:

```
pip install -e .
crude-atdw <command>
```

During development you can also run it without installing, from the `src/` directory:

```
cd src
python3 -m crude_atdw <command>
```

## Config file

The CLI looks for `config.toml` at `~/.config/crude/config.toml` first (honouring `$XDG_CONFIG_HOME`), then falls back to a `config.toml` in the repository root or the current working directory. One file holds every site's section.

Format:

```toml
[atdw]
username = "your-username"
password = "your-password"
```

Credentials are never hard-coded or passed as flags. The JWT token is not stored in this file; it is cached to a durable file under `$XDG_STATE_HOME/crude` (default `~/.local/state/crude/atdw_token`), written atomically with mode `0600`, by the `login` command and on automatic re-authentication. The token survives a reboot; if it is lost, the next command re-authenticates silently from the stored credentials.

A second ATDW account can live in a `[atdw.<name>]` subtable and is selected with `--account/-a <name>` before the resource (or `$CRUDE_ACCOUNT`); see the repository `README.md` for the shared multi-account model. Each account caches its own token: the default account keeps the bare `atdw_token` filename, a named account uses `atdw_token_<name>`.

## Auto-login

If no cached token is present, or if an API call returns 401 (token expired), the CLI re-authenticates using the `username` and `password` from the `[atdw]` section, refreshes the cached token, and retries the request. No manual step is needed. The token is valid for roughly 7 hours.

## `--json` flag

`listing list` and `listing get` accept `--json`. When passed, the raw JSON response is printed to stdout with no truncation, for consumption by AI agents or shell scripts.

## Commands

### `crude-atdw login`

Reads `username` and `password` from the `[atdw]` section, performs the 3-step OAuth2 implicit grant flow against `oauth.atdw-online.com.au`, and caches the resulting JWT to the durable file under `~/.local/state/crude`.

```
crude-atdw login
```

### `crude-atdw listing list [OPTIONS]`

Lists listings. With no filters it returns your organisation's own non-inactive listings (org ID `656826d85c376a10511493fd`), via `GET /listings`. Any filter flag, or `--scope all`, switches to the all-visible search across every listing, via `POST /listings/filter`. Columns: ID, Type, Slug, Status.

```
crude-atdw listing list
crude-atdw listing list --json
crude-atdw listing list --scope all --type tour --limit 5
crude-atdw listing list --scope all --city "Gold Coast" --state QLD
crude-atdw listing list --scope all --name "beach" --json
```

| Flag | Description |
|------|-------------|
| `--scope SCOPE` | `own` (default, your organisation) or `all` (every visible listing) |
| `--type TYPE` | Filter by `listingType` (e.g. `tour`, `attraction`, `foodDrink`, `accommodation`, `event`) |
| `--city CITY` | Filter by `physicalAddress.city_suburb` |
| `--state STATE` | Filter by `physicalAddress.state` |
| `--status STATUS` | Filter by `status`; if omitted, INACTIVE listings are excluded by default |
| `--name TEXT` | Case-insensitive regex match on the `name` field |
| `--limit N` | Maximum number of results (default 20) |
| `--offset N` | Number of results to skip (default 0) |
| `--json` | Print raw JSON array instead of a table |

Filters are combined with LoopBack `and`. For example, `--scope all --type tour --city "Gold Coast"` produces:

```json
{"where":{"and":[{"listingType":"tour"},{"physicalAddress.city_suburb":"Gold Coast"},{"status":{"neq":"INACTIVE"}}]},"limit":20,"skip":0,"order":"slug ASC"}
```

### `crude-atdw listing get ID [--json]`

Shows key fields of a single listing in a two-column table (Field / Value). Works for both owned listings and external listings (other operators).

For owned listings it uses the admin endpoint (`GET /api/listings/:id`), which includes draft content, relations, and admin fields. For external listings it falls back to the published endpoint (`GET /api/listings/:id/publishedListing`), which returns name, description, contacts, and address but not admin-only fields.

For owned listings it also fetches and displays:

- **Media Count**: number of images attached to the listing
- **Services Count**: number of sub-services
- **Tags**: list of tag IDs

```
crude-atdw listing get 6568273cc9320b7770116404
crude-atdw listing get 6568273cc9320b7770116404 --json
```

With `--json`, only the main listing object is printed; the sub-resource counts require separate API calls and are not included.

**Python client:** two methods with distinct semantics:

- `client.get_own_listing(id)`: admin view of an owned listing (401 for external listings)
- `client.get_published_listing(id)`: read-only view of any published listing

### `crude-atdw listing create [--data JSON | -f FILE | stdin] [--yes]`

Creates a new listing (`POST /api/listings`) from a full listing object supplied as JSON via `--data`, `-f/--file`, or piped stdin. ATDW requires at least `listingType`, `category`, `owningOrganisation`, `name`, and `physicalAddress`; `owningOrganisation` defaults to your configured organisation when omitted, and any documented field left out is reported as a warning before the write.

A created listing starts as a **draft** — it is not distributed to the ATDW network until it is reviewed and approved, so run `listing submit` once it is ready. The command prompts for confirmation unless `--yes` is given, and prints the new listing's id and status (`--json` prints the whole created object).

```
crude-atdw listing create -f new-tour.json
crude-atdw listing create --data '{"listingType":"...","category":"...","name":"...","physicalAddress":{...}}' --yes
```

The simplest way to build a valid body is to `listing get <id> --json` an existing listing of the same type and adapt its `listingType`, `category`, `name`, and `physicalAddress`.

**Python client:** `client.create_listing(body)` returns the created object.

### `crude-atdw listing update ID FIELD VALUE`

PATCHes a single field on a listing. Only the named field is sent; all other fields are unchanged. If the value parses as JSON (array or object), it is sent with that type rather than as a string.

```
crude-atdw listing update 6568273cc9320b7770116404 description "New description text"
```

Works for any string field in the listing data model: `description`, `shortDescription`, `name`, `slug`, and so on.

### `crude-atdw listing submit ID`

Submits a listing for ATDW review. Works only on listings with status `DRAFT` or `DRAFTINPROG`. After ATDW approves the listing, its status changes to `ACTIVE`.

This is needed whenever a previously published listing has been edited (which reverts it to `DRAFTINPROG`) or when a new listing is ready for review.

```
crude-atdw listing submit 69b14f64d5bb6b47750392c1
```

If the listing is already `ACTIVE`, the command reports this and exits without making a request.

## API details

- API base: `https://atlas.atdw-online.com.au/api`
- Authentication: OAuth2 implicit grant; JWT bearer tokens (RS512), roughly 7-hour lifetime
- Filter syntax: LoopBack 2/3 JSON filters in query parameters
- Update method: PATCH with only changed fields in the JSON body

See `docs/APIs.md` for the full reverse-engineered API reference including all endpoints, the data model, and enumeration values.
