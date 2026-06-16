# Xero client: command surface and API boundary

Reference for `crude-xero`. Source: Xero's public REST APIs, documented at `https://developer.xero.com/documentation/api/`. Xero is not one API but seven products (Accounting, Payroll, Files, Assets, Projects, BankFeeds, Finance) sharing one OAuth2 transport. This binary ships the **Accounting API** (`api.xro/2.0`) in full, plus the shared auth and tenant machinery; the other six products are registered in the transport's base-path table but have no command coverage yet (see Roadmap). This document covers configuration, the auth and tenant model, the transport, the Accounting command surface, the write conventions, and the boundary of what the binary exposes.

The unit suite (`tests/test_xero.py`, `tests/test_xero_auth.py`) pins the transport (header injection, paging, 401-refresh-retry, 429 back-off, the Xero error shapes), the token store (atomic write, 0600, rotation, never-clobber-on-failure, the config-seed migration), the soft-delete-as-status writes, the cross-cutting attachment whitelist, and `report get <name>` routing.

---

## 1. Configuration

One `[xero]` block in `~/.config/crude/config.toml`:

```toml
[xero]
client_id     = "your-xero-app-client-id"        # developer.xero.com > My Apps > this app
client_secret = "your-xero-app-client-secret"
redirect_uri  = "http://localhost:8910/callback" # a localhost loopback, registered on the app
# scopes is optional; unset requests the default accounting read+write grant (see below).
# scopes = "openid profile email offline_access accounting.transactions accounting.contacts ..."
# default_tenant = "..."  # tenant name or id to use when several organisations are reachable
```

`client_id` and `client_secret` are required. `redirect_uri` must be a `http://localhost:PORT/path` (or `127.0.0.1`) loopback that is also registered as an allowed redirect URI on the Xero app; the loopback server's port is derived from it. When `redirect_uri` is unset the binary falls back to `http://localhost:8910/callback`. When `scopes` is unset it requests a default grant of `openid profile email offline_access accounting.transactions accounting.contacts accounting.settings accounting.attachments accounting.journals.read accounting.reports.read accounting.budgets.read` — the read+write Accounting scopes plus read-only journals/reports/budgets. `default_tenant` pins one organisation; it is written by `crude-xero tenant use` and read at tenant resolution.

**Tokens live in a durable side file, not in config.** Xero rotates the refresh token on every refresh (single-use), access tokens last 30 minutes, and there is no stored password to silently re-login, so the rotating token is kept in `~/.config/crude/xero_token.json` (or `xero_token_<account>.json` for a named account), written atomically (temp file + `fsync` + `os.replace`, mode `0600`) under an `flock`. Any `access_token`/`refresh_token`/`timestamp` left in the `[xero]` config block is read **once** as a migration seed, written into the side file, then ignored; the side file is authoritative thereafter. A naive (non-timezone-aware) seed `timestamp` is treated as already-expired, forcing a refresh while keeping the refresh token.

A second connection — a different Xero app or login — lives in a `[xero.<name>]` subtable and is selected with `--account/-a <name>` (or `$CRUDE_ACCOUNT`); its tokens go to the account-keyed side file.

## 2. Auth and prerequisites

One-time consent, then automatic refresh:

```
crude-xero auth                # open a browser, consent, capture the code on the loopback
crude-xero auth --no-browser   # print the consent URL instead of opening a browser
crude-xero auth --manual       # paste-based flow for a headless box (no local web server)
```

`auth` runs the OAuth2 authorization-code flow: it opens the Xero consent page, serves one request on the redirect URI's loopback port, validates the returned CSRF `state`, and exchanges the code for a token set, which it persists and then prints the connected organisations. `--manual` instead prints the URL and reads back the pasted redirect URL (or bare code), validating `state` when present — for a box with no browser or no reachable loopback.

**Xero developer-portal prerequisites** (developer.xero.com → My Apps → this app), the user's to set up once:

- Add the `redirect_uri` as an allowed redirect URI on the app.
- Enable the OAuth scopes the binary requests. Reads work under read scopes; **writes require the write scopes enabled on the app, then a fresh `crude-xero auth`** to obtain a token carrying them.

Refresh is automatic: the transport refreshes when the access token is within 60 seconds of expiry or on a 401, and persists the rotated token set. The refresh token rotates on every refresh and dies after 60 days idle; if Xero refuses the grant (`invalid_grant`, expired or revoked) the binary errors telling you to run `crude-xero auth` again, and a failed refresh never overwrites the stored token.

## 3. Transport

Every request carries an `Authorization: Bearer <access_token>` header and, except for `/connections`, an `xero-tenant-id` header naming the resolved organisation. The token endpoint authenticates the app over HTTP Basic with `client_id`/`client_secret` (confidential web app, not PKCE).

The seven product base paths, all under `https://api.xero.com/`:

| Product | Base path | Status in this binary |
|---|---|---|
| Accounting | `api.xro/2.0/` | shipped (Phase 1) |
| Files | `files.xro/1.0/` | forthcoming (Phase 2) |
| Assets | `assets.xro/1.0/` | forthcoming (Phase 2) |
| Projects | `projects.xro/2.0/` | forthcoming (Phase 2) |
| Payroll (AU) | `payroll.xro/2.0/` | forthcoming (Phase 3, AU only) |
| BankFeeds | `bankfeeds.xro/1.0/` | forthcoming (Phase 4, partner-gated) |
| Finance | `finance.xro/1.0/` | forthcoming (Phase 4, elevated scope) |

All seven base paths are registered in the transport, but only Accounting has command coverage; the rest are listed for orientation (see Roadmap).

**Paging.** Accounting collections page through the `page` query parameter, 100 records a page, walked until a short page; Journals are the exception — they page by an `offset` cursor set to the trailing `JournalNumber` of the previous chunk (`journal list --offset N` starts after that number). List commands take Xero's own `--where` filter and `--order` sort expressions, except the read-only flat collections (branding themes, budgets, currencies, payment services) which take neither.

**Rate limits.** A `429` is retried once after honouring the `Retry-After` header, bounded at 60 seconds so a single call cannot hang.

**Error shape.** A failed response surfaces a `XeroError` carrying the HTTP status: the top-level `Message` when present, else the joined `Elements[].ValidationErrors[].Message` strings for validation failures, else the OAuth `error_description`/`error`. A `401` surfaces as a `XeroAuthError` after the one refresh-and-retry has been spent.

## 4. Tenant model

Two independent selection axes:

- `--account/-a` (or `$CRUDE_ACCOUNT`) selects the **connection** — which `[xero]` / `[xero.<name>]` credential set, i.e. which OAuth app and login.
- `--tenant/-t` (or `$CRUDE_XERO_TENANT`) selects the **organisation** among the tenants that connection's token can reach (`GET /connections`).

Both go before the resource: `crude-xero --account es --tenant "My Org" invoice list`. A tenant is matched by `tenantId` (uuid) or case-insensitive `tenantName`.

Tenant resolution order: explicit `--tenant` → config `default_tenant` → the sole reachable connection → otherwise an error listing the reachable organisations. The resolved tenant id becomes the `xero-tenant-id` header.

```
crude-xero tenants              # list the organisations the current token can reach
crude-xero tenant use <name|id> # pin one as default_tenant in config (a rare config write)
```

## 5. Command surface

The Accounting resources and their verbs. Read-only resources are marked **(ro)**; verbs in *italics* are the irregular ones described under Write conventions.

| Resource | Verbs |
|---|---|
| `account` | list, get, create, update, delete |
| `bank-transaction` | list, get, create, update |
| `bank-transfer` | list, get, create |
| `batch-payment` | list, get, create, *delete* |
| `branding-theme` **(ro)** | list, get |
| `budget` **(ro)** | list, get |
| `contact` | list, get, create, update, *archive* |
| `contact-group` | list, get, create, update, *member add*, *member remove* |
| `credit-note` | list, get, create, update, *allocate*, *pdf* |
| `currency` | list, create |
| `employee` | list, get, create, update |
| `invoice` | list, get, create, update, *email*, *online-url*, *pdf* |
| `item` | list, get, create, update, delete |
| `journal` **(ro)** | list *(`--offset`)*, get |
| `linked-transaction` | list, get, create, update, delete |
| `manual-journal` | list, get, create, update |
| `organisation` **(ro)** | get |
| `overpayment` | list, get, *allocate* |
| `payment` | list, get, create, *delete* |
| `payment-service` | list, create |
| `prepayment` | list, get, *allocate* |
| `purchase-order` | list, get, create, update, *pdf* |
| `quote` | list, get, create, update, *pdf* |
| `receipt` | list, get, create, update |
| `repeating-invoice` | list, get, create, update, delete |
| `report` **(ro)** | list, *named report* |
| `tax-rate` | list, create, *update* |
| `tracking-category` | list, get, create, update, delete, *option add*, *option update*, *option delete* |
| `user` **(ro)** | list, get |

`report` carries one subcommand per named report rather than a `get <id>`: `balance-sheet`, `profit-and-loss`, `trial-balance`, `aged-receivables`, `aged-payables`, `bank-summary`, `bas`, `gst`, `executive-summary`, `budget-summary` (and `report list` for the available set). Each takes the common report params: `--date`, `--from-date`, `--to-date`, and `--param KEY=VALUE` (repeatable) for any report-specific extras.

**Cross-cutting: attachments and history.** Two generic sub-apps parameterised by the parent object type and its GUID, rather than per-resource verbs on every resource:

```
crude-xero attachment list --on <type> --id <GUID>
crude-xero attachment get  --on <type> --id <GUID> --file <name|id> [--out <path>]
crude-xero attachment add  --on <type> --id <GUID> --file <path> [--mime <ct>]
crude-xero history    list --on <type> --id <GUID>
crude-xero history    add  --on <type> --id <GUID> --note "<text>"
```

`--on` names a friendly singular (`invoice`, `contact`, …) validated against a whitelist before any request; an out-of-set value errors with the valid keys. **The two whitelists differ.** Attachments are supported on: `account`, `bank-transaction`, `bank-transfer`, `batch-payment`, `contact`, `credit-note`, `invoice`, `manual-journal`, `overpayment`, `prepayment`, `purchase-order`, `quote`, `receipt`, `repeating-invoice`. History and notes are supported on: `bank-transaction`, `contact`, `credit-note`, `invoice`, `manual-journal`, `overpayment`, `payment`, `prepayment`, `purchase-order`, `quote`, `receipt`, `repeating-invoice`. (So `account`, `bank-transfer`, and `batch-payment` take attachments but not history; `payment` takes history but not attachments.)

## 6. Write conventions

- **JSON bodies.** `create`, `update`, `allocate`, the membership/option adds, and `tax-rate update` take their body from `--data '<json>'`, `-f/--file <path>`, or piped stdin.
- **Confirmation.** Any write prompts before acting; pass `--yes/-y` to skip (for scripts).
- **Output.** A write prints `<what>: done.` and, when the API returns the affected object, its JSON. `--json` on any verb prints the raw API object instead of a table.
- **Read-merge-write for `update`.** Xero's update is a full-object PUT/POST that rewrites the whole record, so `update` fetches the current object, overlays the `--data`/`-f` JSON, and writes the merged whole back — changing only the fields you supply and leaving the rest intact. (`tax-rate update` is the exception: tax rates have no GUID, so it POSTs the whole `TaxRate` object you provide, with no fetch-and-merge.)
- **Binary output.** `pdf` (on credit-note, invoice, purchase-order, quote) and `attachment get` write raw bytes to `--out <path>`, or to stdout when `--out` is omitted.
- **Soft-deletes are status changes, not hard deletes.** `payment delete` and `batch-payment delete` POST `Status=DELETED`; `contact archive` POSTs `Status=ARCHIVED`. `bank-transaction` has no delete verb (delete it by `update`-ing its `Status` to `DELETED`); `bank-transfer` is immutable after create (no update); `tax-rate` has no delete. Genuine hard deletes (`account`, `item`, `linked-transaction`, `repeating-invoice`, and the tracking-category options) issue an HTTP DELETE.
- **Side effects.** `invoice email` sends real mail to the contact and confirms accordingly.

## 7. Roadmap (not in the current binary)

The other six Xero products are planned for later phases and are **not implemented** in this binary. Each later phase is additive (a new client module, its CLI sub-apps, and the scope strings), reusing the Phase 1 auth and tenant path:

- **Phase 2 — Files, Assets, Projects.** Document storage and associations, fixed-asset register, and the Projects time-tracking product.
- **Phase 3 — Payroll.** **Australian payroll only** (`payroll.xro/2.0`). The New Zealand and UK payroll APIs have different shapes and are not planned.
- **Phase 4 — BankFeeds and Finance.** BankFeeds is **partner-application gated** (needs Xero approval) and Finance needs an **elevated scope**, so both ship last.

## 8. What the API does not expose / caveats

- **AU BAS/GST report.** `report bas` and `report gst` both map to a `BASReport` endpoint name that is **unverified** against the live API; confirm the endpoint name against the Xero reports documentation before relying on it.
- **NZ/UK payroll.** Not planned (Phase 3 is AU-only); only the accounting-side `employee` resource exists here, which is distinct from the Payroll API's employee.
- **Attachment/history coverage is partial.** Only the whitelisted Accounting resources (Section 5) take attachments or history through this binary; other resources, and all non-Accounting products, have none.
- **Dashboard-only features.** Anything Xero exposes only through its web dashboard with no public API endpoint is out of scope; this binary is a thin client over the documented public APIs.
