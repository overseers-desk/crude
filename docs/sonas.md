# Sonas client: protocol, data model, and implementation scope

Reverse-engineered reference and build plan for `crude-sonas`. Source:
`app.sonas.events`, Sonas wedding-venue software by Lytesoft, app v4.58.6,
Meteor 2.16. Discovery date: 2026-06-11.

This document is the single source for the Sonas integration: the wire protocol
and endpoint shapes, the resource map, the data model, and the planned
subcommands. The shipped `crude_sonas` package is a **reference implementation**
of the hard parts (transport, auth, one read resource, the write path); a fresh
implementer should be able to extend it to the rest from this document alone.

Epistemic markers used below: **[live]** = exercised against the real account and
confirmed; **[bundle]** = read from the minified client bundle, not yet run.

---

## 1. Status

Working and verified **[live]**:

- DDP transport (TLS websocket), connect handshake.
- Login (customised Meteor accounts-password with a device fingerprint),
  device-verification handling, and resume-token caching.
- Tenant selection.
- Read: `crude-sonas event list` / `event get` via `eventsByDateRange`.
- Write: `eventUpdateGeneralSection` (a reversible spelling edit on a test event,
  read-verified and restored).

Mapped but not yet built **[bundle]**: every other resource in §6. The argument
shapes there come from each method's `validate()` destructuring in the bundle and
should be confirmed on first call.

---

## 2. Architecture / transport

- **Platform**: Meteor 2.16 single-page app; all data rides **DDP** (Distributed
  Data Protocol, Meteor's publish/subscribe-and-method protocol over a websocket),
  not HTTP.
  There is no public REST API (the vendor confirmed this; `api.sonas.events` is an
  AWS API-Gateway sidecar used only for `/file-upload` and `/image-upload`, plus
  the device-verification redirect in §3).
- **Endpoint**: `wss://app.sonas.events/websocket` (Meteor's raw-DDP websocket;
  not SockJS-framed). Cloudflare fronts it; a normal `User-Agent` and `Origin:
  https://app.sonas.events` on the handshake pass.
- **Framing**: plain DDP JSON messages, one per websocket text frame.
- **Connect handshake**: send `{"msg":"connect","version":"1","support":["1","pre2","pre1"]}`;
  server replies `{"msg":"connected","session":"..."}`.
- **Heartbeats**: server sends `{"msg":"ping","id":...}`; reply `{"msg":"pong","id":...}`.
- **Version coupling**: Meteor autoupdate (`meteor_autoupdate_clientVersions`) hot-pushes
  a matched client to browsers and force-reloads stale ones. A standalone DDP client
  is exposed to silent server-side changes on each Sonas release; treat the method
  and pub shapes here as a snapshot of v4.58.6. Monti APM (`engine.montiapm.com`) records
  client traffic, so automated use is visible to the vendor.

The transport is implemented as free functions in `src/crude_sonas/client.py`
(`ws_connect`, `ws_send`, `ws_recv`, `ddp_connect`, `ddp_call`, `ddp_sub`,
`ddp_unsub`, `_pump`). A connection is a plain dict
`{sock, store, id, session, last}`; `store` is `{collection: {docId: fields}}`,
built from `added`/`changed`/`removed` messages (minimongo-style).

---

## 3. Authentication (built; reference only)

Login is implemented and verified in `auth.py` and `SonasClient._ensure`
(`client.py`); an implementer adding resources reuses it untouched. At a high
level it is a customised Meteor accounts-password login carrying a device
fingerprint, with the Meteor resume token cached in a temp file and reused. Read
the code for the wire details.

One operational note worth knowing while developing: a login from a device
fingerprint or network the server has not seen returns a `verification-error` and
emails a one-time link; opening it once trusts that device, after which logins
resume silently. `sonas_login` detects this and prints guidance. A
`too-many-requests` rate limiter also exists, which the cached resume token avoids.

---

## 4. Tenant selection

Sonas is multi-tenant. After login, **no tenant is selected**, and tenant-scoped
publications return nothing (`recordsTotal: 0`). Call:

```
selectTenant({ docId: "<tenantId>", loginToken: "<resume-token>" })   # method
```

The server then resolves the current tenant via `getCurrentTenantId()` for that
login token. crude auto-discovers `tenantId` from the logged-in user record
(`user.profile.routes[].tenantId`), overridable with `[sonas] tenant`.

---

## 5. Data access patterns

- **Reads are subscriptions, writes are method calls.** `ddp_sub(name, params)`
  collects documents into `store[collection]` until `ready`; `ddp_call(method,
  [arg])` returns the method result or raises on the DDP `error`.
- **EJSON.** Dates arrive as `{"$date": <epoch-ms>}`; encode the same on the way
  in. `client.date_str()` renders them.
- **Direct publications** take typed params, e.g. `eventsByDateRange(from, to)`
  with two EJSON dates. Some publications ship sparse field sets; pass a Mongo
  **projection object** where the pub accepts one (a projection passed as a *list*
  fails with `Match failed`).
- **The aldeed:tabular two-step** drives every list view (`*List`, etc.):
  1. `tabular_getInfo(tableName, selector, sort, skip, limit, searchTerm)` →
     publishes one doc into `tabular_records` with `{ids:[...], recordsTotal,
     recordsFiltered}`.
  2. Subscribe to that table's **data pub** (named in the table's bundle
     definition, e.g. `activitiesWithExtra`) with the ids to receive the
     documents. Signature varies; for the `SystemActivities` table it is
     `dataPub(tableName, ids, projectionObject)`. Try, in order, `[tableName, ids,
     proj]`, `[ids, proj]`, `[tableName, ids]`, `[ids]` until the collection fills.
  - `tabular_getInfo`'s `recordsTotal` is the count for the table's **default
    selector**, not the resource total (e.g. `EventList` defaults to upcoming and
    reports far fewer than `eventsByDateRange` over a wide range). For true counts,
    use the underlying data pub with a wide selector.
- **Unsub discipline.** Meteor dedupes identical `(name, params)` subscriptions per
  connection: re-subscribing after locally clearing the store returns nothing.
  `ddp_unsub(sid)` after collecting, so a later identical read (e.g.
  read-after-write) re-sends. The client's `list_events` does this.

---

## 6. Resource map

Tier (see §8 for the usage basis): **T1** operational (read+write), **T2**
scheduling, **T3** catalog (read-mostly). Method args are the `validate()`
destructured keys; "→" notes the effect. All **[bundle]** unless marked.

### 6.1 Events (T1): the core record

Reads (publications): `eventsByDateRange(from, to)` **[live]** → events in range
(rich docs: status, type, date/endDate, customers, reference, name, currentMain/
currentAdditional guest counts, weddingData, venueId, config). Per-event detail
pubs: `eventCustomersInfo(eventId)`, `eventCosts(eventId)`, `eventTransactions(eventId)`,
`eventFinancialRecords(eventId)`, `eventTermsAndConditions(eventId)`,
`eventServiceBookings(eventId)`, `eventDocs(eventId, documents)`, `eventLayouts(eventId)`,
`eventTables(eventId)`, `eventMessages(eventId)`, `eventActivities(eventId, limit)`,
`eventActivitiesCount(eventId)`, `guests(eventId)`, `enquiryData(eventId)`,
`tastingBookingsForEvent(eventId)`, `eventPricesAndDrinks(eventId)`.

Lifecycle methods (write):
- `eventCreateEnquiry({doc, calendarEventId})`, `eventCreateEnquiryWithMessage({venueId, ...})`,
  `eventCreateConfirmed({customer, event})`
- `eventChangeStatus({eventId, toStatus})`, `eventCanChangeStatus({eventId, toStatus})` (read)
- `eventChangeDate({eventId, date, eventEndDate, areaIds, ceremonyDate})`,
  `eventHoldDate({...same...})`
- `eventPreConfirm({eventId, data, transactions, welcomeTemplateId, termsAndConditions, paymentPlanId, timelineId, fileIds})`
- `eventExhaustEnquiry({eventId, doc})`, `changeEnquiryVenue({eventId, venueId})`
- `eventCancelWithWorkflow({eventId, reasonSlug, note, cancelFutureCharges, revokePortalAccess})`,
  `eventCancelBooking({bookingId})`, `eventDelete({eventId})`, `eventRestore({eventId})`

General update (write): `eventUpdateGeneralSection({modifier, eventId})` **[live]**
(Mongo modifier, e.g. `{$set:{name:...}}`), `eventUpdateCosts({eventId, doc})`,
`eventUpdateWedding({eventMod, eventId, guestModifiers})`,
`eventUpdateCeremonyNotes({eventId, notes})`, `eventUpdateBrandingSection({eventId, doc})`,
`eventSetSectionStatus({eventId, sectionId, status})`, `eventRecalcAggregatedData({eventId})`.

Customers (write): `eventInviteCustomer({eventId, customer})`,
`eventRemoveCustomer({eventId, userId})`, `eventSetCustomerAsMain({eventId, userId})`,
`eventGiveCustomerAccess({eventId, userId, templateId})`,
`eventRevokeCustomerAccess({eventId, userId})`, `eventEditInvitedCustomer({eventId, userId, modifier})`,
`eventUpdateCustomerMarketingPermission({eventId, userId, marketingPermission})`.

Guests (write): `eventAddGuest({eventId, data})`, `eventUpdateGuest({eventId, guestId, modifier})`,
`eventDeleteGuest({eventId, guestId})`, `eventDeleteGuests({eventId, guestIds})`,
`eventImportGuests({eventId, entries, detail})`, `eventUpdateGuestNumbers({eventId, modifier})`,
`eventAssignGuestAttendances({eventId, guestIds, status})`, `eventAssignGuestChoices({eventId, guestIds})`.

Notes (write): `eventAddNote({eventId, text, sectionId})`,
`eventUpdateNote({noteId, text, calendarEventId})`, `eventRemoveNote({noteId})`.

Timeline (write): `eventAddNewTimelineEntry({eventId, entry})`,
`eventEditTimelineEntry({eventId, entryId, entry})`, `eventDeleteTimelineEntry({eventId, timelineEntryId})`,
`eventImportTimeline({eventId, timelineId})`.

Seating / layouts (write): `eventReserveTable({tableId, eventId})`,
`eventFreeTable({eventTableId, eventId})`, `eventReassignGuestToTable({guestId, oldEventTableId, newEventTableId, eventId, newIndex})`,
`eventAddLayout({layout, eventId})`, `eventEditLayout({layoutId, modifier})`, `eventRemoveLayout({layoutId})`.

Menu / drinks (write): `eventChangeFoodMenu({foodMenuId, eventId})`,
`eventChangeDrinksMenu({packageId, eventId})`, `eventUpdateMenuChoice({eventId, choiceData})`,
`eventUpdateDrinkChoice({eventId, choices, notes, serviceTimes})`, `eventSetBarOption({eventId, data, timelineEntries})`.

Documents (write): `eventAddDoc({docId, fileObj})`, `eventDeleteDoc({docId, fileId})`,
`eventChangeDocName({docId, fileId, newName, staffOnly})`, `eventGetDocumentLinks({docId, fileIds})` (read).

Service bookings (write): `eventAddServiceBooking({eventId, serviceId, selectedOptions, questions})`,
`eventEditServiceBooking({eventId, bookingId, selectedOptions, questions})`,
`eventConfirmServiceBooking({eventId, bookingId})`, `eventCancelServiceBooking({eventId, bookingId})`.

Finance (write): `makeChargeTransaction({eventId, doc})`, `makeDiscountTransaction({eventId, doc})`,
`makeRefundTransaction({eventId, doc})`, `makeSecurityDepositTransaction({eventId, doc})`,
`createPaymentTransaction({eventId, financialRecordId, method, amount, description})`,
`createCreditNote({eventId, doc})`, `approveTransaction({transactionId})`, `cancelTransaction({transactionId})`,
`reviseTransaction({transactionId, modifier})`, `generateFinancialRecordDocument({financialRecordId})`,
`toggleEventSkipXeroJournal({eventId})`, `paymentPlanCreate`, `paymentPlanUpdate({docId, modifier})`,
`paymentPlanDelete({docId})`.

Terms (write): `termsCreate({doc})`, `termsDelete({termsId})`, `termsAcceptPending({eventId})`,
`termsAnswer({termsId, answer})`, `termsGeneratePDF({termsId})`.

Messaging (write): `eventCreateDraftMessage({eventId})`, `eventUpdateDraftMessage({messageId, message})`,
`eventSaveMessage({messageId, message})`, `eventSendEmailTemplate({templateId, eventId, userId})`,
`eventMarkMessageAsOpened({eventId, messageId})`.

Activities (write): `eventAddCalledClientActivity({eventId, noteText})`,
`eventVerifyActivity({activityId})`, `eventVerifyAllActivities({eventId})`,
`tenantVerifyActivities(...)`. Reads: the `SystemActivities` table → `activitiesWithExtra`
data pub; the selector for unverified activities is `{verifiedById: null}`.

Export: `exportEvents({clientSelector, extension, mode})`, `tenantImportEvents({entries, detail})`.

### 6.2 Availability & bookings (T2)

Publications: `availabilityByDateRange(from, to)`, `calendarEventsByDateRange(from, to)`,
`calendarEventsByDateRangeForAvailability(from, to, venueIds, availabilityTypes)`,
`calendarEvent(calendarEventId)`, `tastingEventsByDateRange(from, to)`,
`tastingBookingsForEvent(eventId)`, `tastingBookingsByTasting(tastingEventId)`.

Methods: `getVenueAvailability({venueId, eventId, startDate, endDate, eventType})` (read),
`createAvailability({doc})`, `updateAvailability({availabilityId, modifier})`, `deleteAvailability({availabilityId})`,
`calendarEventCreate({doc})`, `calendarEventUpdate({id, modifier})`, `calendarEventDelete({id})`,
`calendarEventSetAttendance({calendarEventId, eventId, attended, noteText})`,
`tastingEventCreate({doc})`, `tastingEventUpdate({tastingEventId, mod})`, `tastingEventDelete({tastingEventId})`,
`tastingEventSetAttendance({tastingEventId, tastingBookingId, attended})`,
`eventAddTastingBooking({previousBookingId, booking})`, `tenantUpdateCalendars({modifier})`,
`exportPublicCalendar({venueId})`, `exportPrivateCalendar({venueId})`.

### 6.3 Leads & enquiry pipeline (T1/T3)

Enquiry option lists (enquiry source, "heard about us", reason-not-booked) are
tag-partitioned entries in the shared `Categories` collection, loaded via the
`initial` pub; CRUD via `categoryAddTag({categoryTag, name})`,
`categoryUpdateTag({categoryId, newName})`, `categoryDeleteTag({categoryId})`,
`categoryRestoreTag({categoryId})`, `categoryMergeTag({srcId, destId})`,
`categoryCheckTagUsage({categoryId})` (read). `categoryTag` ∈ `enquiry_source`,
`heard_about_us`, `reason_not_booked`, `other_venues`.

Web-intake forms: `createTenantExternalForm`, `updateTenantExternalForm({formId, doc})`,
`cloneTenantExternalForm({formId})`, `deleteTenantExternalForm({formId})`.

Reports: `report(reportId)`, `reports(venueId)`, `reportsBasicInfo` (pubs);
`reportCreate({doc})`, `reportUpdate({docId, modifier})`, `reportClone({docId})`,
`reportDelete({docId})`, `reportGenerate({reportId})`. Report types include
`SalesFunnel` and `EventMarketing`.

### 6.4 Catalog & config (T3): read-mostly, method args to confirm on build

Tables/collections (use the tabular two-step or the named pub): `SuppliersList`,
`ServiceList`, `DrinksList`, `PackageList`, `TemplatesList`, `CategoriesList`,
`FormsList`, `VenueList`, `UserList`, `UserRoleList`, `TransactionList`
(`transactionsWithEventDate`), `FinancialRecordsList` (`financialRecordsWithEventDate`),
`Inbox` (`messagesWithExtra`), `ReviewList`, `WorkflowsList`, `AuditLogList`
(`auditLogComposite`, collection `audit-logs`).

To complete a catalog resource, confirm its method arguments primarily by **live
trial** (call the method; the DDP error names the failing `Match`) or by watching
the real call in the logged-in browser's DevTools (Network → WS frames, which show
the method name and payload Sonas sends). To enumerate method and pub *names*
statically, fetch the current Meteor client bundle and grep it. The bundle is the
`<hash>.js?meteor_js_resource=true` script named in the app's page source (no auth
needed); the hash changes each release, so read it fresh:

    curl -s https://app.sonas.events/ | grep -oaE '/[a-f0-9]+\.js\?meteor_js_resource=true'
    curl -s -o /tmp/sonas-bundle.js "https://app.sonas.events/<hash>.js?meteor_js_resource=true"
    grep -oaE 'name:"[a-zA-Z][A-Za-z0-9]+",validate' /tmp/sonas-bundle.js \
      | sed -E 's/.*name:"//; s/",validate//' | sort -u   # method names

A method's `validate()` body in the bundle shows its destructured argument keys.

---

## 7. Data model & enums

`EventStatusEnum`: 0 Enquiry, 1 Confirmed, 2 Cancelled, 3 DateOnHold, 4 Exhausted,
5 ConfirmedPending, 6 Completed, 7 Idle. (Enquiry-group {0,3,4,7}; event-group {1,5,6}; 2 cancelled.)

`EventTypeEnum` (wedding subset): 0 Wedding, 1 Blessing, 10 RenewalOfVows,
13 TwilightWedding, 14 IntimateWedding, 18 Engagement, 19 CommitmentCeremony,
23 Elopement, 32 Reception, 55 CeremonyOnly, 58 WeddingReception (plus non-wedding
types: 2 Corporate, 5 Party, 7 Conference, etc.). `isWedding()` ⊇ {0,1,10,13,14,18,19,22,23,48,50,55,57,58}.

`TransactionKind`: 1 Charge, 2 Payment, 3 Refund, 4 Discount, 5 PaymentMethodFee.
`FinancialRecordType`: 1 Proforma, 2 Invoice, 3 CreditNote.
`PaymentMethod`: 0 Cash, 1 Card, 2 Cheque, 3 Transfer, 4 DirectDebit, 6 OnlineBankTransfer, 100 Other.
`CalendarEventType`: 0 ShowAround, 1 Meeting, 2 Holiday, 3 OpenDay, 5 ItemDelivery,
6 Tasting, 7 Maintenance, 8 PhotoShoot, 9 Accommodation, 10 Ceremony, 11 InternalMeeting, 100 RegularEvent.
`ServiceBooking status`: 1 Pending, 2 Booked, 3 Cancelled.
`AuditLogType` (collection `audit-logs`, field `type`): 1 Insert, 2 Update, 3 Delete.

Event document (from `eventsByDateRange`): `status`, `type`, `date`/`endDate`/`ceremonyDate`
(EJSON), `customers:[{firstname, lastname, main, userId}]`, `reference`, `name`,
`venueId`, `tenantId`, `currentMain`/`currentAdditional`/`includedMain`/`includedAdditional`
(`{adults, teenagers, children, infants, suppliers}`), `areaIds`/`reservedAreaIds`,
`enquiryData`, `weddingData`, `config`.

---

## 8. Usage findings (basis for the tiering)

Measured on the live account 2026-06-11. The team's data and 29-day audit-log
churn show what is operationally live vs static config:

- **34 events** (all time). Active write-churn (29-day audit log): events (84
  changes: status, guest counts, menu, enquiry outcomes), financial-records (20)
  + transactions (15), guests (13), timelines (6), service-bookings (5),
  terms/calendar-events (3 each).
- **Stocked but not edited** (set-once catalog): transactions total 707, notes 186,
  categories 143, service-bookings 88, templates 47, drinks 32, services 20,
  suppliers 6, packages 5, reports 5.
- **Marginal / absent**: reviews 0, platform-contracts 0, user-logins 0, workflows 2,
  forms 4. (Caveat: the audit window is 29 days with a TTL, so "no recent writes"
  means stable config, not unused.)

Implication: build T1 (events + per-event finance/guests/timelines/service-bookings/
notes) and the read side of T3 catalog first; defer reviews/workflows/forms.

---

## 9. CLI subcommand plan

Grammar: `crude-sonas <resource> <verb>`. `--json` on reads; reads are
subscriptions, writes are method calls via `SonasClient.call(method, arg)`. The
resource and verb names below are the proposed CLI surface, not fixed; keep or
adjust them.

**Shipped (reference):** `event list [--from --to --status]`, `event get <id>`.

**T1, operational (read + write):**
- `event`: `list`, `get`, `change-status <id> <status>`, `change-date <id> --date`,
  `hold-date`, `cancel`, `exhaust-enquiry`, `rename <id> --name` (→ `eventUpdateGeneralSection`).
- `guest`: `list <eventId>`, `add`, `update`, `delete`, `set-numbers`.
- `transaction`: `list <eventId>`, `charge`, `payment`, `refund`, `discount`, `approve`, `cancel`.
- `invoice` (financial-record): `list <eventId>`, `get`, `pdf`.
- `service-booking`: `list <eventId>`, `add`, `confirm`, `cancel`.
- `timeline`: `list <eventId>`, `add`, `update`, `delete`, `import`.
- `note`: `list <eventId>`, `add`, `edit <noteId> --text` (→ `eventUpdateNote`), `delete`.
- `message`: `list <eventId>`, `send --template`.
- `document`: `list <eventId>`, `add`, `delete`.
- `terms`: `list <eventId>`, `accept`, `pdf`.
- `activity`: `list <eventId>`, `verify <activityId>`, `verify-all <eventId>`.

**T2, scheduling:** `availability list`, `appointment list|create|update|delete`
(calendar-event), `tasting list|book|cancel`.

**T3, catalog (read-only first):** `supplier`, `service`, `drinks-package`
(→ `DrinksList`), `package` (→ `PackageList`), `template`, `category`, `report`,
`venue`, `user`, all `list`/`get`.

**Skip / defer:** reviews, platform-contracts, workflows, forms.

---

## 10. Reference implementation & how to extend

`src/crude_sonas/`:
- `client.py`: DDP transport (free functions) plus `SonasClient` (session, creds,
  tenant, resource methods). `SonasClient.call(method, arg)` is the write primitive;
  `list_events`/`get_event` show the read pattern and the unsub discipline.
- `auth.py`: `sonas_login` (login plus device-verification guidance) and
  `sonas_resume`.
- `cli.py`: Typer app, `register_claude_command`, `_make_client`, render helpers.

To add a resource: add a read method to `SonasClient` (subscribe to its pub or run
the tabular two-step, collect from `store`, unsub), add write methods as
`self.call("<method>", {<args from §6>})`, then add a Typer sub-app in `cli.py`
mirroring the `event` commands. Confirm each method's arg shape on first call (the
DDP error names the failing match).

Two conventions the `event` commands set: list output is a Rich table plus
`--json` (the `_render_*`/`_emit` helpers in `cli.py`), and destructive verbs
(`event cancel`, deletes) should prompt for confirmation unless `--yes`, as
`crude-deputy resource delete` does (`typer.confirm(..., abort=True)`).

---

## 11. crude integration checklist

Already wired for `crude-sonas`: `pyproject.toml` `[project.scripts]`,
`crude_common/launcher.py` `SITES` + help, `crude_common/claude_command.py`
(`description`, config-sections line, the `## crude-sonas` block),
`config.example.toml` `[sonas]`, `debian/control`, `crude.spec` (entry-point loop,
`%files`, `%description`), `formula/crude.rb` `%w[]`. When a release is cut, bump
`pyproject.toml` `version` and the mirrors per `docs/RELEASING.md`; add a
`debian/changelog` entry.

---

## 12. Setup

1. Fill in the `[sonas]` section following `config.example.toml`.
2. The first run from a new device or network triggers the one-time
   device-verification email (§3); open the link once, and later runs resume from
   the cached token.

---

## 13. Open gaps / uncertainties

- Method args behind composed/external validators (e.g. `eventCreateEnquiry`'s
  `doc`, `calendarEventCreate`'s `doc`, `paymentPlanCreate`) are not destructured
  in the bundle. Confirm them by live trial (the DDP error names the failing
  `Match`) or by watching the real call in the logged-in browser's DevTools
  (Network → WS frames show the exact method name and payload). The nested CLI
  payloads in §9 (a charge's `doc`, a guest's `data`, a timeline `entry`) resolve
  the same way.
- `EventSectionEnum` member names are known (General, Wedding, Guests, MenuChoice,
  Timeline, Seating, Bar, ...) but integer values were not decoded; `eventAddNote`'s
  `sectionId` value is therefore unconfirmed.
- T3 catalog data-pub names and method args still need bundle-mining (§6.4).
- EJSON encoding of write arguments beyond plain strings/ids (dates, nested docs)
  is unverified; the one confirmed write used a `{$set:{name}}` string modifier.
- Tabular data-pub signatures vary per table; use the try-in-order approach in §5.
