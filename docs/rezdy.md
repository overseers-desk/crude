# Rezdy client: command surface and API boundary

Reference for `crude-rezdy`. Source: the Rezdy **Supplier** API, documented at `https://developers.rezdy.com/rezdyapi/index-supplier.html` (OpenAPI 3.0, ReDoc-rendered). Unlike the ATDW and Sonas clients, this one rides a documented public API, so this document covers the CLI surface, the write conventions, and the boundary of what the API exposes, rather than a reverse-engineered protocol.

`crude_rezdy` implements every resource the Supplier API exposes. The non-live unit suite (`tests/test_rezdy_writes.py`, `tests/test_accounts_and_timezone.py`, `tests/test_rezdy_retry.py`, `tests/test_rezdy_paging.py`, `tests/test_rezdy_export.py`) pins the transport and its back-off, the read-merge-write, the booking notification default, the resolver, the whole-list walks and the export; the live read paths run under `pytest -m live` when a config is present.

---

## 1. Configuration

One `[rezdy]` block in `~/.config/crude/config.toml`:

```toml
[rezdy]
api_key = "your-rezdy-supplier-api-key"   # Rezdy: Integrations > Rezdy API > Request API Key
timezone = "Australia/Brisbane"           # required, IANA name
environment = "production"                # optional; "staging" hits api.rezdy-staging.com
```

`timezone` is required: a typed date (`--from`, `--updated-to`, …) is read as that account's operational day before any UTC comparison. A second account lives in a `[rezdy.<name>]` subtable and is selected with `--account/-a <name>` (or `$CRUDE_ACCOUNT`).

## 2. Transport and auth

Base `https://api.rezdy.com/v1` (`https://api.rezdy-staging.com/v1` for staging). The API key is sent in the `apiKey` request header, which keeps it out of request URLs and so out of any error message that quotes one. Rezdy reports failure on two channels — the HTTP status and a `requestStatus` object in the body — and the client surfaces either as a `Rezdy API error`. Rate limit is 100 requests/minute: the client spaces its requests to at most 80 in any minute (the limit counts every program using the key, so the rest is left to them), and retries with back-off (2, 4, 8, 16, 32 seconds, or the wait Rezdy names in `Retry-After`, five retries at most) when Rezdy refuses for the minute's count and on the HTTP 500 it gives when a call of its own fails (`DOWNSTREAM_CALL_FAILURE`). Rezdy's refusal for the count is not an HTTP 429: it arrives as HTTP 406 with `errorCode` 3 and the message `Too Many Requests (limit=100, current count=…)`, and crude retries both. The 500 is retried for reads only: on a write it does not say whether the change landed, and sending it again could book twice. Any other error is raised at once. The pacing is per process, so two commands running together share Rezdy's limit and can still meet the refusal, which the retry absorbs.

How each list answers is not the same, and a list that does not page cuts off silently:

| List | Paging |
|------|--------|
| `product`, `customer`, `voucher`, `category`, `category products`, `resource` lists | `offset`/`limit`, at most 100 a page |
| `availability list` | `offset`/`limit`, at most 1000 a page |
| `booking list` | `offset`/`limit`, at most 100 a page |
| `resource sessions` | **none.** Rezdy answers at most 100 sessions and ignores both `limit` and `offset` (checked against the live API, October 2026): every "page" is the same first 100. The only way past 100 is a smaller date window. |
| `extra list` | **none.** Rezdy answers a search with at most 100 extras and ignores `limit` and `offset`. The search string is a prefix match that ignores case; an empty one returns 100, the newest first. |
| `rate list`, `pickup-list list`, `product pickups` | take search terms only, as documented; no sign of a cap (an account with fewer than 100 cannot show one) |

`--all` on a list verb walks it to the end: `product`, `customer`, `voucher`, `category`, `category products`, `resource` and `availability` list walk the pages (`--limit` and `--offset` are ignored), stopping on a short page or on a full page with nothing new in it, which is what an endpoint that ignores `offset` would answer; `resource sessions --all` and `extra list --all` use the means below.

## 3. Command surface

| Resource | Verbs |
|----------|-------|
| `product` | list *(`--all`)*, get, create, update, delete, image-add, image-remove, pickups |
| `availability` | list *(`--product` repeats; `--all-products`; `--all`)*, create, update, delete, batch |
| `booking` | list, cancellations, get, quote, create, update, cancel |
| `customer` | list *(`--all`)*, get, create, delete |
| `extra` | list *(`--search`, `--all`; no paging)*, get, create, update, delete |
| `pickup-list` | list *(`--search`)*, get, create, update, delete |
| `category` | list *(`--all`)*, get, products *(`--all`)*, add-product, remove-product |
| `rate` | list *(`--name`, `--product`)*, get, add-product, remove-product |
| `resource` | list *(`--all`)*, sessions *(`--all`)*, for-session, add-session, remove-session |
| `manifest` | order-status, order-set, order-remove, session-status, session-set, session-remove |
| `voucher` | list *(`--search`; empty returns all; `--all`)*, get *(by code, read-only)* |
| `company` | get *(by alias)*, find *(by name)* |
| *(account)* | `export <dir>` *(read-only; see below)* |

`booking list` follows the booking search as Rezdy's changelog entry of 16 June 2026 defines it. `--search` is a slow prefix match on customer names and payment or voucher codes; matching an order number or an agent code through it is deprecated, so an order goes to `booking get` and an agent's bookings to `--source-channel <agent code>`. `--reseller-reference` (the agent's own booking number), `--role` and a repeated `--product` are the other server-side filters. `category list` takes `--search` and `--visible/--private`. `resource sessions` takes a local `--from/--to` window, in which Rezdy returns the sessions that both start and end inside it, at most 100 of them; it does not page, so `--offset` has no effect (crude says so on stderr when it is given, and when a call returns the full 100). `resource sessions --all` needs both bounds and walks the window in months, halving any window that returns 100, until each returns fewer; sessions are kept once by id and come back in start order. Rezdy returns the earliest 100 of a window, and only sessions that start and end inside it, so a session running over a cut would lie whole in neither half: neighbouring windows overlap by a margin, a day at the month cuts and elsewhere the longest session seen so far (an hour at least, twelve hours at most). A session longer than the margin that runs over a cut can still be missed. A window under four margins that still returns 100 cannot be cut further: crude prints what it has, names the window on stderr and exits 1.

`extra list` cannot be paged either. `extra list --all` takes the means the API allows: a search that fills the 100 is split by the next character (a to z, 0 to 9) and each part searched in turn, to eight characters deep, about 40 requests for an account of a few hundred extras. An extra whose name starts with anything but a letter or a digit is out of that walk's reach unless it is among the unfiltered first 100; `export` finds those by the names the products carry (`extras_named` in the client), which a search for the whole name does reach.

`availability list --product` repeats for several products, or `--all-products` lists every product of the account; several products give one flat list of sessions, each carrying its `productCode` (a Product column in the table). `--all-products` walks every page of every product, as `--all` does for the products named.

`category` and `rate` writes assign or unassign a product to an existing category/rate; the categories and rates themselves are read-only in the API. `resource` writes assign or unassign a session; resources themselves are read-only — the API has no endpoint to create one, so a new resource (vehicle, room, guide, animal, …) is added in the dashboard and then assigned here.

A few paths are not the obvious guess and follow the spec: a session is mutated by product code and local start time (`availability update --product P1 --start-local "..."`), not a session id; `manifest *-set` toggles check-in (`--checkin/--no-checkin`) for an order-session or a whole session, keyed by `--product` and `--start`/`--start-local`; `rate list` and the search-based `extra`/`pickup-list` lists take their documented query terms rather than offset paging (for the extras, see above). Custom booking questions are not a separate resource: they are the product's `bookingFields` array, edited through `product update` — and because that overlay replaces the list wholesale rather than merging it, you send the complete set of fields, not just the new one.

### Export

`crude-rezdy export <dir> --from <day> --to <day>` writes the whole account as JSON files in a directory, using only list and get calls. Each file holds what the matching `--json` verb prints, walked to the end by the means above:

| File | Contents |
|------|----------|
| `products.json`, `product-detail.json`, `product-pickups.json` | the product list; each product's full record; each product's pickups, keyed by product code |
| `company.json` | the company record of each `supplierAlias` the products carry, keyed by alias |
| `availability.json` | every session of every product in the window, keyed by product code |
| `bookings.json` | every booking, whatever its dates (the window does not apply) |
| `customers.json`, `vouchers.json` | the full lists |
| `extras.json` | the extras: the prefix walk, plus any a product carries that the walk could not reach, found by name |
| `pickup-lists.json`, `pickup-list-detail.json`, `rates.json`, `rate-detail.json` | the lists and each record by id |
| `categories.json`, `category-products.json` | the categories; each category's products, keyed by id |
| `resources.json`, `resource-sessions.json` | the resources; each resource's sessions in the window, by `resource sessions --all`, keyed by id |
| `manifest.json` | when it was made, the timezone, the window (and the parts it applies to), the parts exported, each file's counts, `errors`, `gaps` and `complete` |

`--from/--to` (a local day or time, read as on `resource sessions`) bound the availability and the resource sessions, and are required unless `--only` leaves those parts out. `--only` (repeatable: `products`, `availability`, `bookings`, `customers`, `extras`, `pickup-lists`, `categories`, `rates`, `resources`, `vouchers`, `company`) exports some parts only. A call that still fails after the retries is recorded in `errors` and the export goes on; a window of resource sessions that cannot be cut is recorded in `gaps`. `complete` is true when both are empty, and the exit code is 1 when it is not. The directory is created readable by its owner only, and so are the files: they hold customers and bookings. An export refuses under `WORLD_AS_OF`, since it reads the account as it stands now. The requests are spaced to at most 80 a minute, so a large account takes minutes, not seconds: the resource sessions are the long part (one resource with 23,479 sessions took 680 requests, some eight minutes, against the live API), and each part is reported on stderr as it is written.

## 4. Time bounds

Rezdy's booking search takes ISO 8601 instants, both ends inclusive, and reads a value without a zone as UTC: a bare date is 00:00:00Z, so a day sent raw as both bounds is a single instant (10:00 in Brisbane) and a range loses the start of its first local day and most of its last. `booking list` therefore converts what is typed. A bare date on `--from/--to`, `--created-from/--created-to` or `--updated-from/--updated-to` becomes the first or the last second of that day in the account's `timezone`; a time typed without an offset is account-local too, as on `availability list`, and `Z` or an offset is honoured.

`--updated-from`, and `booking cancellations --from`, go to Rezdy as `updatedSince`, so every page returned is already in range. Rezdy leaves out a booking stamped exactly at that value, so crude asks from one second earlier and the typed bound stays inclusive. Rezdy has no upper update bound: `--updated-to` and the cancellations `--to` are checked on the pages fetched, so `--all` matters when one is used alone. A booking that was never updated carries no `dateUpdated`; Rezdy leaves it out of `updatedSince` results and crude leaves it out of an upper-bound window. The Updated and Cancelled On columns show the account-local day.

## 5. Write conventions

- **JSON bodies.** `create`, `quote`, `batch`, and the full-object `update` verbs take their body from `--data '<json>'`, `-f/--file <path>`, or piped stdin.
- **Confirmation.** A write that creates or destroys prompts before acting; pass `--yes/-y` to skip (for scripts).
- **Output.** A write prints `<what>: done.` and, when the API returns the affected object, its JSON. `--json` on any verb prints the raw API object with no table.
- **Read-merge-write.** `product update`, `extra update`, and `pickup-list update` fetch the current object, overlay the typed flags and `--data`, and write the merged whole back — so `product update P1 --terms "..."` changes only the terms and leaves the rest intact. A flag left unset is not part of the change; an explicit empty string clears the field. `availability update` and `booking update` send the body directly: the API has no single-session read to merge against, and a booking update accepts only status, customer, and participants.
- **Booking notifications.** `booking create` sends `sendNotifications=true` by default, so Rezdy emails the customer as it normally does. `--no-notify`, or `sendNotifications=false` in `--data`, suppresses the emails, but only after a confirmation whose default answer is no (the booking is then not created); `--yes` skips the ordinary create prompt but never this one, so suppressing customer emails is always a deliberate answer. `--no-notify` wins over a `sendNotifications=true` in `--data`.

## 6. Terms & Conditions

T&C is the product's `terms` field, not a separate resource. Edit it through the product:

```
crude-rezdy product update P1 --terms "Full refund up to 48h before departure."
crude-rezdy product get P1 --json | jq .terms
```

## 7. ID resolution

Most list endpoints already return human names alongside their codes (a booking item carries `productName`, a category's products carry `name`). Where a command works from a bare product code, the name is resolved for confirmation: `availability list --product P1 ...` prints the product's name above the sessions. The resolver caches the product list for the process.

## 8. What the public API does not expose

These are operator-dashboard features with no public Supplier API endpoint, so `crude-rezdy` does not implement them:

- **Creating vouchers, coupons, or promo codes.** Vouchers are read-only here (`voucher list`, `voucher get`); the API states outright that coupons (voucher, promocode) are not creatable, and creating `GIFT_CARD` products is prohibited.
- **Email / notification templates.** There is no template endpoint; bookings carry only the `sendNotifications` on/off flag.

Reaching these means driving the Rezdy operator dashboard's private, session-authenticated API — the reverse-engineering class of the ATDW and Sonas clients — which needs a captured dashboard session and is a separate effort from this public-API client.
