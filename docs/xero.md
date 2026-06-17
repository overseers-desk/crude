# Xero client: command surface and API boundary

Reference for `crude-xero`. Source: Xero's public REST APIs, documented at `https://developer.xero.com/documentation/api/`. Xero is not one API but seven products (Accounting, Payroll, Files, Assets, Projects, BankFeeds, Finance) sharing one OAuth2 transport. This binary ships all seven: the **Accounting API** (`api.xro/2.0`) in full, plus **Files**, **Assets**, **Projects**, **Payroll** (AU only), **BankFeeds**, and **Finance**, over the shared auth and tenant machinery. BankFeeds and Finance are **access-gated**: built and wired, but usable only after Xero grants restricted API access to the app (see Section 5). This document covers configuration, the auth and tenant model, the transport, the command surface, the write conventions, and the boundary of what the binary exposes.

The unit suite (`tests/test_xero.py`, `tests/test_xero_auth.py`) pins the transport (header injection, paging, 401-refresh-retry, 429 back-off, the Xero error shapes), the token store (atomic write, 0600, rotation, never-clobber-on-failure, the config-seed migration), the soft-delete-as-status writes, the cross-cutting attachment whitelist, and `report get <name>` routing. Each non-Accounting product has its own suite (`tests/test_xero_files.py`, `tests/test_xero_assets.py`, `tests/test_xero_projects.py`, `tests/test_xero_payroll.py`, `tests/test_xero_bankfeeds.py`, `tests/test_xero_finance.py`), pinning that product's distinct list-envelope unwrap, its base path, and the HTTP method of each verb (the Files multipart upload, the Payroll POST-to-create/update, the BankFeeds batch envelopes, the create/delete routings).

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

`client_id` and `client_secret` are required. `redirect_uri` must be a `http://localhost:PORT/path` (or `127.0.0.1`) loopback that is also registered as an allowed redirect URI on the Xero app; the loopback server's port is derived from it. When `redirect_uri` is unset the binary falls back to `http://localhost:8910/callback`. When `scopes` is unset it requests a default grant of `openid profile email offline_access accounting.transactions accounting.contacts accounting.settings accounting.attachments accounting.journals.read accounting.reports.read accounting.budgets.read files assets projects payroll.employees payroll.payruns payroll.payslip payroll.timesheets payroll.settings`: the read+write Accounting scopes plus read-only journals/reports/budgets, and the read+write Files, Assets, Projects, and Payroll AU scopes (read is implied by each write scope). The BankFeeds and Finance scopes are deliberately left out of the default, because those products are access-gated and requesting their scopes before Xero grants access returns `invalid_scope` and breaks `crude-xero auth` (see Section 5). `default_tenant` pins one organisation; it is written by `crude-xero tenant use` and read at tenant resolution.

**Tokens live in a durable side file, not in config.** Xero rotates the refresh token on every refresh (single-use), access tokens last 30 minutes, and there is no stored password to silently re-login, so the rotating token is kept in `$XDG_STATE_HOME/crude/xero_token.json` (default `~/.local/state/crude/xero_token.json`; or `xero_token_<account>.json` for a named account), written atomically (temp file + `fsync` + `os.replace`, mode `0600`) under an `flock`, seeded once from the `[xero]` config tokens if present. A rotating OAuth token is XDG *state*: it persists across restarts, but is not config (it is program-managed, not user-authored) and not cache (losing it costs a browser re-consent). Any `access_token`/`refresh_token`/`timestamp` left in the `[xero]` config block is read **once** as a migration seed, written into the side file, then ignored; the side file is authoritative thereafter. A naive (non-timezone-aware) seed `timestamp` is treated as already-expired, forcing a refresh while keeping the refresh token.

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
- Enable the OAuth scopes the binary requests. Reads work under read scopes; **writes require the write scopes enabled on the app, then a fresh `crude-xero auth`** to obtain a token carrying them. When a call is refused for want of a scope (a 403, or a 401 that survives a token refresh), the error names the scopes the token actually carries and tells you to add the missing one to `scopes` and re-run `crude-xero auth`; re-auth alone does not widen a grant.

Refresh is automatic: the transport refreshes when the access token is within 60 seconds of expiry or on a 401, and persists the rotated token set. The refresh token rotates on every refresh and dies after 60 days idle; if Xero refuses the grant (`invalid_grant`, expired or revoked) the binary errors telling you to run `crude-xero auth` again, and a failed refresh never overwrites the stored token.

## 3. Transport

Every request carries an `Authorization: Bearer <access_token>` header and, except for `/connections`, an `xero-tenant-id` header naming the resolved organisation. The token endpoint authenticates the app over HTTP Basic with `client_id`/`client_secret` (confidential web app, not PKCE).

The seven product base paths, all under `https://api.xero.com/`:

| Product | Base path | Status in this binary |
|---|---|---|
| Accounting | `api.xro/2.0/` | shipped (Phase 1) |
| Files | `files.xro/1.0/` | shipped (Phase 2) |
| Assets | `assets.xro/1.0/` | shipped (Phase 2) |
| Projects | `projects.xro/2.0/` | shipped (Phase 2) |
| Payroll (AU) | `payroll.xro/2.0/` | shipped (Phase 3, AU only) |
| BankFeeds | `bankfeeds.xro/1.0/` | shipped, but **access-gated** (Phase 4, see below) |
| Finance | `finance.xro/1.0/` | shipped, but **access-gated** (Phase 4, see below) |

All seven products have command coverage. BankFeeds and Finance are wired but access-gated: Xero must grant the app restricted API access before their scopes can be consented (see Section 5).

**Paging.** Accounting collections page through the `page` query parameter, 100 records a page. A `list` returns the first page by default; `--all` walks every page, and `--limit N` collects up to N records across pages (`--all` wins if both are given). Journals are the exception — they page by an `offset` cursor set to the trailing `JournalNumber` of the previous chunk (`journal list --offset N` starts after that number). List commands take Xero's own `--where` filter and `--order` sort expressions, except the read-only flat collections (branding themes, budgets, currencies, payment services) which take neither.

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

### Files (`files.xro/1.0`)

The Files product stores documents and folders and associates a file with an accounting object. Read-only resources are marked **(ro)**; verbs in *italics* are irregular (described below the table).

| Resource | Verbs |
|---|---|
| `file` | list, get, update, delete, *upload*, *content* |
| `folder` | list, get, create, update, delete |
| `association` | *list*, *object*, *add*, *remove* |
| `inbox` **(ro)** | get |

- `file upload` sends the file as `multipart/form-data` (the one verb a JSON body cannot carry): `file upload --file <path> [--name <stored>] [--folder <id>] [--mime <ct>]`. The stored name defaults to the basename and the MIME type is guessed from the filename when omitted; without `--folder` the file lands in the root.
- `file content` downloads the raw bytes to `--out <path>`, or to stdout when omitted.
- `association list <file-id>` lists the objects a file is associated with; `association object <object-id>` lists the files associated with an accounting object; `association add <file-id>` takes a JSON body (`ObjectId`, `ObjectType`, `ObjectGroup`); `association remove <file-id> <object-id>` disassociates the pair.

### Assets (`assets.xro/1.0`)

The Fixed Assets register. Assets and asset types are create-only (the API exposes no update or delete); settings is read-only.

| Resource | Verbs |
|---|---|
| `asset` | *list*, get, create |
| `asset-type` | list, create |
| `asset-settings` **(ro)** | get |

- `asset list` requires a status filter: `--status DRAFT|REGISTERED|DISPOSED` (default `REGISTERED`), and pages on the Assets product's own `{pagination, items}` envelope via `--page` and `--limit` (the page size, `pageSize`), with `--order-by` for the sort field.

### Projects (`projects.xro/2.0`)

The Projects time-tracking product: projects, their nested tasks and time entries, and the read-only project-users list. Tasks and time entries are scoped to a project via `--project <id>`.

| Resource | Verbs |
|---|---|
| `project` | list, get, create, *update* |
| `task` | list, get, create, update, delete |
| `time-entry` | list, get, create, update, delete |
| `project-user` **(ro)** | list |

- `project update` is a PATCH status change rather than a full-object read-merge-write: pass `--status <state>` (e.g. `INPROGRESS`, `CLOSED`) or a JSON `--data` body.
- `task` and `time-entry` verbs take `--project <id>` (the parent) plus the child id as an argument where one is needed. Their `list` accepts `--page`/`--page-size`; `project list` also takes `--states` and `--contact` filters. `update` on a task or time entry is the usual read-merge-write PUT.

### Payroll (`payroll.xro/2.0`)

Payroll runs on the unified, camelCase Payroll platform (the API shape Xero shares across AU, NZ, and UK), not the classic PascalCase Payroll AU product. Every response wraps its payload in a metadata envelope (`{id, providerName, dateTimeUTC, httpStatusCode, pagination:{page,pageSize,pageCount,itemCount}, problem, <camelCasePlural>}`) and dates are ISO strings. A create POSTs to the collection and an update POSTs to the element (Payroll has no PUT), unlike Accounting's PUT-to-create. Read-only resources are marked **(ro)**.

| Resource | Verbs |
|---|---|
| `pay-employee` | list, get, create, update |
| `pay-run` | list, create |
| `pay-run-calendar` | list, get, create |
| `earnings-rate` | list, get, create, update |
| `reimbursement` | list, get, create, update |
| `timesheet` | list, get, create, update, delete |
| `payroll-settings` **(ro)** | get |

- `pay-run` is list/create only. The single-pay-run detail (`GET PayRuns/{id}`) and the element update are not served (405/404), and with them go the payslip lines, so there is no `get`, no `update`, and no per-run payslip read (Section 8). A run's totals come from the list. Note: `GET PayRuns?page=2` is observed to return HTTP 500 server-side (other pages return normally), so `pay-run list --all` can fail partway past two pages; fetch a bounded page set with `--limit`.
- `earnings-rate` and `reimbursement` are this platform's separate resources for what the classic API grouped under a single `PayItems` object.
- `payroll-settings` is a read-only singleton wrapping the linked payroll accounts.

### BankFeeds (`bankfeeds.xro/1.0`)

Bank-feed connections and the statements pushed against them. **Access-gated**: the commands exist, but a `bankfeeds` consent is refused until Xero grants the app access (see the note below). Verbs in *italics* are irregular.

| Resource | Verbs |
|---|---|
| `feed-connection` | list, get, create, *delete* |
| `statement` | list, get, create |

- Both `list` verbs page on `--page`/`--page-size`.
- `create` on either resource takes a batch body `{"items":[{...}]}`. `feed-connection delete` POSTs a delete-request batch of the same shape, because BankFeeds exposes no HTTP DELETE.

### Finance (`finance.xro/1.0`)

Cash validation, bank-statement accounting, financial statements, and accounting-activity reports. The whole product is **read-only**. **Access-gated**: the commands exist, but a `finance.*` consent is refused until Xero grants the app access (see the note below). Verbs in *italics* are the named-endpoint groups.

| Resource | Verbs |
|---|---|
| `cash-validation` **(ro)** | get |
| `bank-statement` **(ro)** | get |
| `financial-statement` **(ro)** | *named statement* |
| `activity` **(ro)** | *named activity* |

- `cash-validation get` takes `--balance-date`; `bank-statement get` takes `--bank-account`, `--from-date`, `--to-date`, and `--summary-only`.
- `financial-statement` carries one command per statement (`balance-sheet`, `profit-loss`, `cash-flow`, `trial-balance`) and `activity` one per accounting activity (`account-usage`, `lock-history`, `report-history`, `user-activities`). Each builds its query from `--date`/`--from-date`/`--to-date` plus `--param KEY=VALUE` (repeatable), mirroring the Accounting `report` group.

### Access-gating: BankFeeds and Finance

BankFeeds and Finance need Xero to grant the app **restricted API access** before their scopes can be consented. Until that grant, a consent that includes a `finance.*` or `bankfeeds` scope is rejected (`crude-xero auth` fails with `invalid_scope`; confirmed for `finance.statements.read`), so those scopes are kept out of the default grant. The commands are built and wired, and begin working as soon as access is granted: add the relevant scopes to config `scopes` and run `crude-xero auth` again.

- BankFeeds: `bankfeeds`
- Finance: `finance.statements.read finance.cashvalidation.read finance.accountingactivity.read finance.bankstatementsplus.read`

## 6. Write conventions

- **JSON bodies.** `create`, `update`, `allocate`, the membership/option adds, and `tax-rate update` take their body from `--data '<json>'`, `-f/--file <path>`, or piped stdin.
- **Confirmation.** Any write prompts before acting; pass `--yes/-y` to skip (for scripts).
- **Output.** A write prints `<what>: done.` and, when the API returns the affected object, its JSON. `--json` on any verb prints the raw API object instead of a table.
- **Read-merge-write for `update`.** Xero's update is a full-object PUT/POST that rewrites the whole record, so `update` fetches the current object, overlays the `--data`/`-f` JSON, and writes the merged whole back — changing only the fields you supply and leaving the rest intact. (`tax-rate update` is the exception: tax rates have no GUID, so it POSTs the whole `TaxRate` object you provide, with no fetch-and-merge.)
- **Binary output.** `pdf` (on credit-note, invoice, purchase-order, quote) and `attachment get` write raw bytes to `--out <path>`, or to stdout when `--out` is omitted.
- **Soft-deletes are status changes, not hard deletes.** `payment delete` and `batch-payment delete` POST `Status=DELETED`; `contact archive` POSTs `Status=ARCHIVED`. `bank-transaction` has no delete verb (delete it by `update`-ing its `Status` to `DELETED`); `bank-transfer` is immutable after create (no update); `tax-rate` has no delete. Genuine hard deletes (`account`, `item`, `linked-transaction`, `repeating-invoice`, and the tracking-category options) issue an HTTP DELETE.
- **Side effects.** `invoice email` sends real mail to the contact and confirms accordingly.

## 7. Roadmap

All seven products are implemented; their command surfaces are in Section 5. The build landed in additive phases, each a new client module, its CLI sub-apps, and the scope strings, reusing the Phase 1 auth and tenant path:

- **Phase 1, Accounting.** The full `api.xro/2.0` resource surface, plus the auth and tenant machinery every later phase reuses.
- **Phase 2, Files, Assets, Projects.** Document storage and associations, the fixed-asset register, and the Projects time-tracking product. Added the `files assets projects` scopes to the default grant.
- **Phase 3, Payroll.** The `payroll.xro/2.0` unified Payroll platform (camelCase, the shape Xero shares across AU, NZ, and UK, not the classic PascalCase AU product). Added the `payroll.employees payroll.payruns payroll.payslip payroll.timesheets payroll.settings` scopes to the default grant.
- **Phase 4, BankFeeds and Finance.** Wired but **access-gated**: Xero must grant the app restricted API access before their scopes can be consented, so their scopes stay out of the default grant (see Section 5). Even once Finance access is granted, bank **reconciliation cannot be driven programmatically**: the public APIs expose no endpoint to mark a bank transaction reconciled (reconciliation is a dashboard-only action), and Finance's BankStatementsPlus read (`bank-statement get`) is the closest reconciliation-relevant data the API offers.

## 8. What the API does not expose / caveats

- **AU BAS/GST report.** `report bas` and `report gst` both map to a `BASReport` endpoint name that is **unverified** against the live API; confirm the endpoint name against the Xero reports documentation before relying on it.
- **Unified Payroll platform.** Payroll runs on the `payroll.xro/2.0` unified, camelCase API (the shape shared across AU, NZ, and UK), not the classic PascalCase AU product. The Accounting API's `employee` resource is distinct from the Payroll API's `pay-employee`.
- **Payslips and pay-run detail.** The Payroll API serves no single-pay-run read (`GET PayRuns/{id}` → 405) and no payslip endpoint (`Payslips` → 404/405). Payslip-level data (which staff were paid in a given run, and each one's gross and net) is therefore not reachable through the API; `pay-run list` gives the runs and their period totals only. The `payroll.payslip` scope is requested but currently has no served endpoint.
- **Attachment/history coverage is partial.** Only the whitelisted Accounting resources (Section 5) take attachments or history through this binary; other resources, and all non-Accounting products, have none.
- **Dashboard-only features.** Anything Xero exposes only through its web dashboard with no public API endpoint is out of scope; this binary is a thin client over the documented public APIs.
