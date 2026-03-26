# pyatdw — User Manual

Python CLI wrapper for the ATDW (Australian Tourism Data Warehouse) REST API.

## Installation (development)

Dependencies are managed in `pyproject.toml`. Required packages: `typer[all]`, `requests`, `tomli_w` (plus `tomli` on Python < 3.11, which uses the stdlib `tomllib`). Rich is pulled in by `typer[all]` and used for table output.

Since the package is not pip-installed, run all commands from the `src/` directory:

```
cd /path/to/pyatdw/src
python3 -m pyatdw <command>
```

## Config file

The CLI looks for `config.toml` by walking up from the package file, then falling back to the current working directory. For development use, place it in the project root.

Format:

```toml
[auth]
username = "your-username"
password = "your-password"
# token is written here automatically after login
token = "eyJ..."
```

The `token` field is written by the `login` command (and automatically on 401) and read by all other commands. Credentials are never hard-coded or passed as flags.

## Auto-login

If no token is present in `config.toml`, or if an API call returns 401 (token expired), the CLI automatically re-authenticates using the `username` and `password` from `config.toml`, updates the `token` field in the file, and retries the request. No manual intervention is needed. The token is valid for approximately 7 hours.

## `--json` flag

`listings`, `listing`, and `search` all accept `--json`. When passed, the raw JSON response is printed to stdout with no truncation. This is intended for machine consumption by AI agents or shell scripts.

## Commands

### `atdw login`

Reads `username` and `password` from `config.toml`, performs the 3-step OAuth2 implicit grant flow against `oauth.atdw-online.com.au`, and caches the resulting JWT under `[auth] token` in `config.toml`.

```
python3 -m pyatdw login
```

### `atdw listings [--json]`

Lists all non-inactive listings for the organisation (org ID `656826d85c376a10511493fd`). Displays a table with columns: ID, Type, Slug, Status.

```
python3 -m pyatdw listings
python3 -m pyatdw listings --json
```

### `atdw listing ID [--json]`

Shows key fields of a single listing in a two-column table (Field / Value). Works for both owned listings and external listings (other operators).

For owned listings, uses the admin endpoint (`GET /api/listings/:id`) which includes draft content, relations, and admin fields. For external listings, automatically uses the published endpoint (`GET /api/listings/:id/publishedListing`) which returns name, description, contacts, and address but not admin-only fields.

In addition to the core listing fields, the command also fetches and displays (owned listings only):

- **Media Count** — number of images attached to the listing
- **Services Count** — number of sub-services
- **Tags** — list of tag IDs

```
python3 -m pyatdw listing 6568273cc9320b7770116404
python3 -m pyatdw listing 6568273cc9320b7770116404 --json
```

With `--json`, only the main listing object is printed (sub-resource counts are not included, as they require separate API calls).

**Python client:** Two methods with distinct semantics:

- `client.get_own_listing(id)` — admin view of an owned listing (401 for external listings)
- `client.get_published_listing(id)` — read-only view of any published listing

### `atdw edit ID FIELD VALUE`

PATCHes a single field on a listing. Only the specified field is sent in the request body; all other fields are unchanged.

```
python3 -m pyatdw edit 6568273cc9320b7770116404 description "New description text"
```

Works for any string field in the listing data model: `description`, `shortDescription`, `name`, `slug`, etc.

### `atdw search [OPTIONS]`

Searches across all visible listings (not restricted to the owning organisation). All flags are optional and can be combined. Omitted flags are not included in the filter.

```
python3 -m pyatdw search --type tour --limit 5
python3 -m pyatdw search --city "Gold Coast" --limit 5
python3 -m pyatdw search --type tour --city "Gold Coast" --state QLD
python3 -m pyatdw search --name "rivermill" --json
```

| Flag | Description |
|------|-------------|
| `--type TYPE` | Filter by `listingType` (e.g. `tour`, `attraction`, `foodDrink`, `accommodation`, `event`) |
| `--city CITY` | Filter by `physicalAddress.city_suburb` |
| `--state STATE` | Filter by `physicalAddress.state` |
| `--status STATUS` | Filter by `status`; if omitted, INACTIVE listings are excluded by default |
| `--name TEXT` | Case-insensitive regex match on the `name` field |
| `--limit N` | Maximum number of results (default 20) |
| `--json` | Print raw JSON array instead of a table |

The filters are combined with LoopBack `and`. For example, `--type tour --city "Gold Coast"` produces:

```json
{"where":{"and":[{"listingType":"tour"},{"physicalAddress.city_suburb":"Gold Coast"},{"status":{"neq":"INACTIVE"}}]},"limit":20,"order":"slug ASC"}
```

## API details

- API base: `https://atlas.atdw-online.com.au/api`
- Authentication: OAuth2 implicit grant; JWT bearer tokens (RS512), ~7-hour lifetime
- Filter syntax: LoopBack 2/3 JSON filters in query parameters
- Update method: PATCH with only changed fields in the JSON body

See `docs/APIs.md` for the full reverse-engineered API reference including all endpoints, data model, and enumeration values.
