# Rezdy client: command surface and API boundary

Reference for `crude-rezdy`. Source: the Rezdy **Supplier** API, documented at `https://developers.rezdy.com/rezdyapi/index-supplier.html` (OpenAPI 3.0, ReDoc-rendered). Unlike the ATDW and Sonas clients, this one rides a documented public API, so this document covers the CLI surface, the write conventions, and the boundary of what the API exposes, rather than a reverse-engineered protocol.

`crude_rezdy` implements every resource the Supplier API exposes. The non-live unit suite (`tests/test_rezdy_writes.py`, `tests/test_accounts_and_timezone.py`) pins the transport, the read-merge-write, the booking notification default, and the resolver; the live read paths run under `pytest -m live` when a config is present.

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

Base `https://api.rezdy.com/v1` (`https://api.rezdy-staging.com/v1` for staging). The API key is sent as the `apiKey` query parameter. Rezdy reports failure on two channels — the HTTP status and a `requestStatus` object in the body — and the client surfaces either as a `RezdyClient API error`. Rate limit is 100 requests/minute. List endpoints page by `offset`/`limit` (limit max 100); `booking list --all` walks the pages.

## 3. Command surface

| Resource | Verbs |
|----------|-------|
| `product` | list, get, create, update, delete, image-add, image-remove, pickups |
| `availability` | list, create, update, delete, batch |
| `booking` | list, cancellations, get, quote, create, update, cancel |
| `customer` | list, get, create, delete |
| `extra` | list *(`--search`)*, get, create, update, delete |
| `pickup-list` | list *(`--search`)*, get, create, update, delete |
| `category` | list, get, products, add-product, remove-product |
| `rate` | list *(`--name`, `--product`)*, get, add-product, remove-product |
| `resource` | list, sessions, for-session, add-session, remove-session |
| `manifest` | order-status, order-set, order-remove, session-status, session-set, session-remove |
| `voucher` | list *(`--search`; empty returns all)*, get *(by code, read-only)* |
| `company` | get *(by alias)*, find *(by name)* |

`category` and `rate` writes assign or unassign a product to an existing category/rate; the categories and rates themselves are read-only in the API. `resource` writes assign or unassign a session; resources themselves are read-only — the API has no endpoint to create one, so a new resource (vehicle, room, guide, animal, …) is added in the dashboard and then assigned here.

A few paths are not the obvious guess and follow the spec: a session is mutated by product code and local start time (`availability update --product P1 --start-local "..."`), not a session id; `manifest *-set` toggles check-in (`--checkin/--no-checkin`) for an order-session or a whole session, keyed by `--product` and `--start`/`--start-local`; `rate list` and the search-based `extra`/`pickup-list` lists take their documented query terms rather than offset paging. Custom booking questions are not a separate resource: they are the product's `bookingFields` array, edited through `product update` — and because that overlay replaces the list wholesale rather than merging it, you send the complete set of fields, not just the new one.

## 4. Write conventions

- **JSON bodies.** `create`, `quote`, `batch`, and the full-object `update` verbs take their body from `--data '<json>'`, `-f/--file <path>`, or piped stdin.
- **Confirmation.** A write that creates or destroys prompts before acting; pass `--yes/-y` to skip (for scripts).
- **Output.** A write prints `<what>: done.` and, when the API returns the affected object, its JSON. `--json` on any verb prints the raw API object with no table.
- **Read-merge-write.** `product update`, `extra update`, and `pickup-list update` fetch the current object, overlay the typed flags and `--data`, and write the merged whole back — so `product update P1 --terms "..."` changes only the terms and leaves the rest intact. A flag left unset is not part of the change; an explicit empty string clears the field. `availability update` and `booking update` send the body directly: the API has no single-session read to merge against, and a booking update accepts only status, customer, and participants.
- **Booking notifications.** `booking create` sets `sendNotifications=false` by default, so a test order emails no one; `--notify` turns it on and is authoritative over any `sendNotifications` in `--data`.

## 5. Terms & Conditions

T&C is the product's `terms` field, not a separate resource. Edit it through the product:

```
crude-rezdy product update P1 --terms "Full refund up to 48h before departure."
crude-rezdy product get P1 --json | jq .terms
```

## 6. ID resolution

Most list endpoints already return human names alongside their codes (a booking item carries `productName`, a category's products carry `name`). Where a command works from a bare product code, the name is resolved for confirmation: `availability list --product P1 ...` prints the product's name above the sessions. The resolver caches the product list for the process.

## 7. What the public API does not expose

These are operator-dashboard features with no public Supplier API endpoint, so `crude-rezdy` does not implement them:

- **Creating vouchers, coupons, or promo codes.** Vouchers are read-only here (`voucher list`, `voucher get`); the API states outright that coupons (voucher, promocode) are not creatable, and creating `GIFT_CARD` products is prohibited.
- **Email / notification templates.** There is no template endpoint; bookings carry only the `sendNotifications` on/off flag.

Reaching these means driving the Rezdy operator dashboard's private, session-authenticated API — the reverse-engineering class of the ATDW and Sonas clients — which needs a captured dashboard session and is a separate effort from this public-API client.
