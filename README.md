# pyatdw

The Australian Tourism Data Warehouse (ATDW) provides a web interface for tourism operators to manage their listings — descriptions, photos, opening hours, contact details, and so on. The interface is adequate for occasional manual edits, but it does not lend itself to bulk operations, scripted updates, or integration with other tools. pyatdw is a command-line client that talks directly to the ATDW REST API, so that listings can be queried and edited from the terminal or from automated workflows.

The tool is deliberately narrow. It authenticates, lists, shows, searches, and edits listings. It does not attempt to replicate every feature of the web interface.

## Setup

Copy the example config and fill in your ATDW credentials:

```
cp config.example.toml config.toml
```

Edit `config.toml` with your username and password. The file is gitignored.

## Dependencies

Python 3.8+.

**Ubuntu/Debian:**

```
sudo apt-get install python3-typer python3-rich python3-requests python3-tomli python3-tomli-w
```

`python3-tomli-w` is in the `universe` repository; enable it with `sudo apt-get install software-properties-common && sudo add-apt-repository universe` if the package is not found.

**OS X** — these packages are not in Homebrew; use pip:

```
pip3 install "typer[all]" requests tomli tomli-w
```

## Usage

Run from the `src/` directory:

```
cd src
python3 -m pyatdw <command>
```

### Login

```
python3 -m pyatdw login
```

Reads credentials from `config.toml`, authenticates via the ATDW OAuth2 flow, and caches the JWT token. The token lasts approximately 7 hours. If it expires, any subsequent command will re-authenticate automatically using the stored credentials.

### List own listings

```
python3 -m pyatdw listings
```

Returns a table of all non-inactive listings belonging to the organisation:

```
 ID                         Type        Slug                          Status
 69b14f64d5bb6b47750392c1   event       cowboys-&-country             ACTIVE
 6568273cc9320b7770116404   foodDrink   historic-rivermill            ACTIVE
 6903586bb6fc9ea77eaaed84   attraction  historic-rivermill            ACTIVE
 696600aa66821ba339fb2b05   accommodation historic-rivermill-farmstay DRAFTINPROG
 67891ac1f4c999b32e8d8672   event       pisco-sour-day-...            EXPIRED
```

### Show a single listing

```
python3 -m pyatdw listing 6568273cc9320b7770116404
```

Displays key fields (name, type, status, description, dates) plus media count, services count, and tags.

### Search across all listings

```
python3 -m pyatdw search --type tour --limit 5
python3 -m pyatdw search --city "Gold Coast" --limit 5
python3 -m pyatdw search --name "rivermill"
python3 -m pyatdw search --type tour --city "Gold Coast" --state QLD
```

Searches are not restricted to the owning organisation. Flags can be combined freely; omitted flags impose no constraint. Available filters: `--type`, `--city`, `--state`, `--status`, `--name` (regex), `--limit`.

### Edit a listing field

```
python3 -m pyatdw edit 6568273cc9320b7770116404 description "New description text"
```

Sends a PATCH with only the named field. Works for any string field in the data model (`description`, `shortDescription`, `name`, etc.).

### JSON output

All read commands accept `--json` to emit raw JSON instead of a formatted table, for machine consumption:

```
python3 -m pyatdw listings --json
python3 -m pyatdw listing 6568273cc9320b7770116404 --json
python3 -m pyatdw search --city "Gold Coast" --json
```

## Further reference

- `docs/manual.md` — full command reference with flag tables and filter syntax
- `docs/APIs.md` — reverse-engineered ATDW API reference
