# WORLD_AS_OF for crude — implementation design

Design only; no code changed. Written 2026-07-13 against crude 1.4.1.

## Problem

`WORLD_AS_OF` is an office-wide environment variable holding an ISO-8601 instant with timezone (e.g. `2026-07-12T17:07:00+10:00`). When set, a tool may not emit anything dated after that instant, so a benchmark or replay session sees the world as it stood then. Three semantics: unset means unbounded; set means bound queries at the server where possible and drop or refuse anything newer, with a clear message; set-but-unparseable is a hard failure, since a silently ignored bound produces a contaminated run that looks valid. Web/browser tools are out of scope. Crude is in scope, including any data it pre-parses for an AI's opening prompt.

Crude fronts nine third-party backends, most of which keep no history: a booking's current state overwrote "just booked", a Sonas event was edited in place, a roster changed. The deliverable here is a reasonable, documented per-backend boundary, not a pretence that mutable stores can be rewound.

The operator fixed that boundary in the originating requirement: "a booking is either closed or served no longer just booked, or an event has been updated, that's fine; there has to be a reasonable boundary to get data and not everything can be done by limiting server query." So serving a pre-cutoff record in its current (post-cutoff-mutated) state, flagged, is an accepted requirement rather than an author default: where this doc later attributes a boundary to "the operator" (Rezdy `dateUpdated`, the Sonas event body), it means this directive, and a maintainer should not tighten those flags to a drop without the operator revisiting it.

## Discoveries that shape the design

1. **No single request choke point.** `crude_common.httpapi.HttpSession._request` covers only ATDW, Airwallex, Clover, and Deputy. Rezdy, Xero, Skål (Odoo JSON-RPC), and Sonas (Meteor DDP) each have their own single central request method; Facebook uses plain Graph calls. Enforcement therefore lives at two layers: one shared as-of module in `crude_common` (parse, clamp, post-filter, refuse helpers), invoked per backend at its own choke point and at its date-argument handling.
2. **Only one pre-filled-prompt path exists**: `crude-sonas event export`, which dumps the full enquiry corpus (one JSON per event plus `index.json`) for AI consumption. It is status-bounded, not time-bounded, and today captures each event's entire lifetime. The `install-claude-command` doc is a byte-for-byte static string with no live data, so it needs nothing.
3. **Date-filter support is uneven.** Airwallex exposes `from_created_at`/`to_created_at` on every list. Clover orders filter on `createdTime`/`modifiedTime`. Rezdy bookings accept `maxDateCreated` server-side but have no updated-since server param. Deputy's QUERY endpoint accepts arbitrary field clauses, and Deputy objects carry `Created`/`Modified` audit fields the code does not yet read. Xero supports `where=UpdatedDateUTC<=...` on accounting lists and `If-Modified-Since`, neither used today. Skål's Odoo models carry `create_date`/`write_date`, deliberately not fetched today, and Odoo domains can filter on them. Sonas has `*ByDateRange` pubs for events/availability/appointments/tastings but per-event pubs return everything. ATDW's LoopBack filters could in principle bound `updatedOn` but no date filter is wired. Facebook Graph supports `since`/`until` on feed edges but the code passes neither.
4. **Two timestamp regimes exist per record**: creation time (append-only fact, exact under a bound) and last-modified time (evidence of post-cutoff mutation, not recoverable history). The honest rule throughout: **exclude records created after the cutoff; serve the current state of records created before it; flag any record whose modified-time is after the cutoff as post-cutoff-mutated.** No backend here offers point-in-time reads, so the pre-cutoff record body is best-effort current state, disclosed as such.

## Core mechanics (crude_common)

A new module, say `crude_common/asof.py`:

- `world_as_of() -> datetime | None`. Reads `WORLD_AS_OF` once. Unset returns None (zero cost, zero behaviour change). Set but unparseable (no timezone offset counts as unparseable) raises a dedicated error; every site CLI's root callback calls this before any request, so a bad value aborts the process with a clear message and nonzero exit. Hard failure, no fallback.
- `clamp_upper(to_instant) -> instant`: min(user-supplied upper bound, WORLD_AS_OF), for wiring into each backend's server-side date params. When the user's `--to`/`--from` window starts after the cutoff, refuse with a message rather than return an empty-looking result.
- `post_filter(records, created_key, modified_key=None, parse=...)`: drops records whose creation timestamp exceeds the bound (counting dropped rows), and marks records whose modified timestamp exceeds it. Per-backend timestamp parsing (ISO, epoch-ms, EJSON `{"$date": ms}`, Odoo strings) is passed in.
- Messaging: one stderr line per command when the bound is active, e.g. `WORLD_AS_OF 2026-07-12T17:07+10:00: 12 records created after cutoff dropped; 3 records mutated after cutoff served as current state`. In `--json` output, mutated-after-cutoff records additionally carry a `"_world_as_of": "mutated-after-cutoff"` key so a machine consumer can discount them. Plain-table output relies on the stderr notice alone; per-cell markers would be noise.
- **Writes refuse.** A replay session reads the past; a write mutates the live present and contaminates both the run and the real store. When `WORLD_AS_OF` is set, `crude_common.writeio.do_write` (and the non-writeio write paths: Sonas DDP method calls, Xero writes, Facebook writes) refuse with a message. This is the cheapest rule in the design and closes the largest contamination hole.

`crude_common/localtime.to_utc_iso` converts typed `--from/--to` dates for several CLIs, but Rezdy (config IANA zone) and Sonas (venue-local EJSON) convert their own, and the conversion is not uniform. So the clamp (`asof.clamp_upper_iso`) is applied per backend at each list call rather than hooked into one shared place; `localtime` itself is untouched by the change. A bound placed only in `localtime` would silently miss the two backends that bypass it.

## Per-backend boundaries

Each entry states: read surfaces, store character, enforcement mode (server filter / post-filter / current-state-flagged), and the honest rule.

### Airwallex (documented REST; the cleanest)

Financial transactions, balance history, beneficiaries, transfers, conversions, and all `pa` lists already accept `from_created_at`/`to_created_at`; the ledger objects are effectively append-only at creation, though status fields mutate. **Server filter:** clamp `to_created_at` to the bound on every list; `get <id>` post-filters on `created_at` and refuses if newer. Records carry `updated_at`; flag when it exceeds the bound (a transfer created before the cutoff whose status settled after it is served in its settled state, flagged). `balance current` and `fx-rate current` are inherently now-valued with no history: **refuse under a bound** (balance history bounded by `posted_at` is the substitute). `account get` is slow-moving current state: serve, flagged.

### Clover (documented REST)

Orders: **server filter** via `filter=createdTime<={bound_ms}`; the `--since` incremental mode (`modifiedTime>=`) is incompatible with a bound (it is explicitly a live-sync tool) and refuses. The `--since` high-water-mark state file is not written under a bound. Payments/refunds/credits carry `createdTime`: **post-filter** (their list endpoints take the generic `filter` param, so server-side `createdTime<=` is likely wirable too; verify against the AP API before relying on it). Catalog (items, categories, modifier groups) and the rest of the registry are mutable current-state with no usable audit filter: **current-state-flagged** — serve as-is with the stderr flag that catalog reflects now, not the cutoff instant.

### Rezdy (documented supplier API)

Bookings: **server filter** on creation via `maxDateCreated=bound`; a booking created before the cutoff but updated after (cancelled, amended, served) is served in its current state and flagged via `dateUpdated > bound` (the operator's stated acceptable boundary). `booking cancellations` filters `dateUpdated` client-side today; under a bound it post-filters the same way. Availability requires an explicit time window: clamp `endTimeLocal`; note availability is a live inventory count and cannot show past remaining-seats, so it is **current-state-flagged** even inside the window (or refused for windows at/after the cutoff — refusal is truer, since "availability as of last week" is unanswerable). Products, extras, pickups, categories, rates, resources, vouchers, company: mutable catalog, no audit filter surfaced: **current-state-flagged**. Vouchers carry `issueDate`: post-filter on it.

### Deputy (documented resource API)

Every object carries `Created`/`Modified` audit fields the code does not yet request; the QUERY endpoint accepts arbitrary clauses. **Server filter:** inject `Created le {bound}` into every QUERY-based list (rosters, timesheets, leave, employees, generic `resource query`); post-filter `GET /resource/{obj}` unqueried lists and `get <id>` on the returned `Created`. Flag `Modified > bound` (a roster row created before the cutoff and edited after is the canonical mutable case; current state, flagged). `me` is session identity: serve unflagged. Business dates (`Date`, `StartTime`) are the domain timeline, not the knowledge timeline; the bound acts on `Created`/`Modified`, and a roster dated next week but entered before the cutoff is correctly visible.

### Xero (documented OAuth2 API)

Accounting collections carry `UpdatedDateUTC` and the API filters on it. Xero exposes no created-date filter on most collections, so the exactness split differs from the others: **server filter** `where=UpdatedDateUTC<={bound}` excludes anything touched after the cutoff, which over-excludes (a pre-cutoff invoice edited yesterday disappears) rather than serving mutated state. That is the conservative choice and the right default for a benchmark: absence is honest, a silently newer body is not. Journals are append-only with sequential `JournalNumber` and a `CreatedDateUTC`: **exact** via post-filter (or server `where`), the best surface in the whole tool. Reports and finance statements accept `date`/`fromDate`/`toDate`: clamp them; reports over pre-cutoff periods are still computed from today's ledger, so a post-cutoff back-dated edit leaks in: **flagged** as computed-now-over-past-period. Projects, payroll, bankfeeds, assets, files lists have no date params: **post-filter** on each payload's `UpdatedDateUTC`-equivalent where present, current-state-flagged where absent. User-supplied `--where` composes with the injected clause via `AND`.

### Sonas (Meteor DDP, no public API; hardest, and the prompt-corpus owner)

Range-driven reads (`event list`, `availability list`, `appointment list`, `tasting list`): the `*ByDateRange` pubs bound the domain date (wedding date, appointment start), not knowledge time. Clamping `to` at the bound is wrong for events (a wedding next year enquired-about last year is legitimately visible) and right for nothing except display windows, so the range params stay user-controlled and the knowledge bound applies as **post-filter on creation timestamps**: event docs via `enquiryData.date` (enquiry creation) where present, else the earliest `createdAt` in the bundle, else current-state-flagged; messages, notes, activities, documents via `createdAt`; terms via `answeredAt`; transactions and financial records lack a reliable creation stamp beyond `createdAt` where present, else flagged. The event document itself is mutated in place on every status change ("an event has been updated, that's fine"): pre-cutoff-created events are served in current state and flagged. Per-event pubs return full history unbounded; all bounding is client-side by construction of DDP.

**`event export` (the pre-filled-prompt corpus):** enumeration stays status-driven (dateless enquiries are the point of it), and each bundle is passed through the same post-filter: drop messages/transactions/financial-records/notes created after the bound, drop whole events whose enquiry creation is after the bound, keep pre-cutoff events with their current-state doc flagged in the bundle (`"_world_as_of"` key) and a summary in `index.json`. This closes the boundary for pre-parsed prompt data: the corpus handed to a model contains nothing created after the cutoff, and everything mutable in it is marked.

### Facebook (Graph API)

`post list`: Graph supports `until` on feed edges; **server filter** `until={bound}` plus a post-filter on `created_time` as belt-and-braces (verify `published_posts` honours `until`; if not, pure post-filter, and stop paginating once a page is entirely older than the bound since the edge is reverse-chronological). `post get`, `comment list`: **post-filter** on `created_time`. `--scheduled` posts are future-dated by nature: refuse under a bound. Insights (post and page) are rolling aggregates recomputed over current data with no per-row timestamp: **refuse** — an impression count fetched today is not the count as of the cutoff, and there is no honest flag that fixes a number. `page get` (follower counts etc.): current-state-flagged; the counts are now-values, disclosed as such.

### ATDW (portal-internal LoopBack API)

Listings are pure mutable documents; only `publishedOn`/`updatedOn` surface, no creation date. LoopBack `where` could take `updatedOn lte`, which over-excludes like Xero's; for a small own-org listing set the better trade is **current-state-flagged**: serve listings, flag any with `updatedOn > bound`, and drop none (a tourism listing's identity is stable; its body drifts). `--scope all` search behaves the same. This is the weakest boundary in the tool and is documented as such.

### Skål (Odoo JSON-RPC via member portal)

Odoo keeps `create_date`/`write_date` on every model; the client deliberately omits them today. Under a bound, add them to the requested fields and inject `["create_date", "<=", bound]` into every domain: **server filter**, exact on creation. Flag `write_date > bound`. Members, clubs, events, benefits all follow the same rule through the single `_search_read` choke point, making Skål one of the easier backends despite being a scraped portal.

### Summary table

| Backend | Server-side bound | Post-filter | Current-state-flagged | Refused under bound |
|---|---|---|---|---|
| Airwallex | all lists (`to_created_at`) | `get` by `created_at` | `account get`; `updated_at`>bound | `balance current`, `fx-rate current` |
| Clover | orders `createdTime<=` | payments/refunds/credits | catalog, registry | `--since` mode |
| Rezdy | bookings `maxDateCreated` | vouchers, cancellations | products/extras/rates etc.; `dateUpdated`>bound | availability at/after cutoff |
| Deputy | QUERY `Created le` | plain lists, `get` | `Modified`>bound | — |
| Xero | accounting `where UpdatedDateUTC<=`; journals exact; report date params | projects/payroll etc. where stamps exist | reports (computed-now); stamp-less lists | — |
| Sonas | — (DDP) | `createdAt`-family per collection; export bundles | event doc bodies | — |
| Facebook | posts `until` (verify) | posts, comments by `created_time` | `page get` | insights, scheduled posts |
| ATDW | — | — | listings; `updatedOn`>bound | — |
| Skål | Odoo domain `create_date<=` | — | `write_date`>bound | — |
| all | — | — | — | **every write verb** |

## Feasibility verdict

**Yes, this can be done well.** The shared machinery is small; the work is nine thin per-backend adaptations over choke points that already exist (one request/date-conversion path per backend). Effort: **M overall** — roughly S for `crude_common/asof.py` + write refusal + tests, S–M for the four backends with real server filters (Airwallex, Clover, Deputy, Skål), M for the post-filter and flagging work across Rezdy, Xero, Sonas (including the export), Facebook, ATDW, and documentation.

Sharpest risks:

1. **Unverified server behaviour.** Whether Facebook `published_posts` honours `until`, whether Clover payments accept a `createdTime` filter, and whether Skål's Odoo instance permits `create_date` in domains from the portal session are assumptions from API family knowledge, not from this codebase. Each needs one live probe before its stage is committed (the `live` pytest marker exists for exactly this).
2. **Over-exclusion vs contamination on Xero.** Filtering `UpdatedDateUTC<=` hides pre-cutoff records edited later. That is the chosen conservative default, but a benchmark comparing against a historical answer that referenced such a record will see a hole. The flag-instead-of-drop alternative is a one-line switch per collection; the choice should be recorded per collection when implemented, not globalised.
3. **Knowledge time vs domain time confusion.** Rosters, weddings, and availability are dated in the future relative to any cutoff; the bound acts on when the record entered the world, not when its subject occurs. Every per-backend filter has to pick the right field, and picking the domain date silently empties legitimate results. The per-backend field table above is the guard; tests should include a future-dated-but-pre-cutoff-created record per backend.
4. **Client-side paging cost.** Backends with post-filter-only bounds (Sonas per-event pubs, Rezdy updated-side, Facebook comments) fetch everything then drop; on large accounts this is slow but correct. No mitigation needed beyond the reverse-chronological early-stop where ordering guarantees it.
5. **The unparseable-value gate has to sit before the first network call in every entry path** (nine CLIs plus the launcher), or a bad value part-runs before failing. The shared root callback pattern already exists in every CLI, which is where the parse belongs.

## Staging (so a later session can resume and implement)

Each stage is a coherent commit and independently shippable; nothing depends on a later stage.

1. **Core:** `crude_common/asof.py` (parse with hard failure, clamp, post-filter, stderr notice, `_world_as_of` JSON marker), parse wired into every site CLI's root callback and the launcher, write refusal in `writeio.do_write` and the non-writeio write paths. Tests in a new `tests/test_asof.py` covering the three semantics, plus timezone-less values failing. This stage alone makes a set-but-unparseable bound safe office-wide.
2. **Exact server filters:** Airwallex (`to_created_at` clamp everywhere), Clover orders, Rezdy bookings (`maxDateCreated`), Deputy (`Created le` injection, audit fields requested), Skål (domain injection, audit fields fetched). One live probe each behind the `live` marker.
3. **Post-filter backends:** Xero (`where` injection, journals, report-param clamping, computed-now flag), Facebook (`until` probe, `created_time` post-filter, insights refusal), Rezdy `dateUpdated` flagging, refusals for the inherently-now surfaces (balances, fx, availability, `--since`).
4. **Sonas, including the corpus export:** collection-by-collection `createdAt`-family post-filters, event-body flagging, and the bounded `event export`. This stage closes the pre-filled-prompt path.
5. **ATDW flagging and documentation:** `updatedOn` flag, a WORLD_AS_OF section in `docs/manual.md` stating the per-backend boundary table verbatim (the operator-facing honesty contract), and a line in the Claude command doc's static text noting the variable's effect.

Resume note for the implementing session: the four survey conclusions in "Discoveries" above were established by reading every `src/crude_*` package on 2026-07-13; endpoint and field names quoted here (e.g. Deputy `Created`, Rezdy `maxDateCreated`, Sonas `enquiryData.date`, Xero `UpdatedDateUTC`) were taken from that code and the backends' documented APIs, and the ones marked "verify" in risk 1 are the only load points not yet observed live.

## As built: residual boundaries and deferred work

The plan above shipped across commits `b266e82`..`02c108a`, with the byte-path and write-probe closures in the review that followed. This section records what a later session still owns; everything else is closed and tested (mock transports, isolated-config binary runs, no network).

**Server-side clauses ride belt-and-braces client filters, so no live probe gates correctness.** Four backends inject a server clause whose exactness is not observed in this codebase: Facebook `published_posts?until=`, Clover orders `createdTime<=`, Skål's Odoo `create_date <=` domain, and Deputy QUERY `Created le`. Each is an early-exclusion optimization only: the same records are dropped again by an exact client-side post-filter (`bound_records` / `post_filter`) after fetch, so a server that silently ignores the clause is slower, never wrong. The remaining work is one `live`-marked test per clause confirming the server honours it (a performance and cost check, not a correctness one). Until then the clause is trusted but not relied upon.

**Byte artifacts are gated on a metadata read, not a flag.** A PDF or attachment download returns opaque bytes that cannot carry `_world_as_of`, so `AccountingAPI._pdf` and `get_attachment` perform one metadata GET first and apply the record's stamp rule (`deny_newer`): a record created or modified after the cutoff refuses before any bytes leave. This costs one extra GET per download under a bound only. Xero report/statement PDFs are bounded earlier, by their clamped date params.

**Sonas PDF generation refuses as a write, not a read.** `terms pdf` (`termsGeneratePDF`) and `invoice pdf` (`generateFinancialRecordDocument`) are DDP method calls, which the client's `guard_write` refuses under a bound; they generate a portal artifact, so refusing is both correct and stricter than a stamp check would be. No separate byte gate is needed for them.

**`list_attachments` is a metadata list served current-state.** It returns the attachments a record carries now, with no per-row creation stamp; bounding it exactly would require fetching the parent per row. It is left as an unflagged current-state read — the lowest-severity residual surface, noted here rather than closed.
