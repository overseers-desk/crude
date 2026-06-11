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
- Write: the event lifecycle (§6.1): `eventCreateEnquiry`, `eventHoldDate`,
  `eventChangeDate`, `eventChangeStatus`, `eventExhaustEnquiry`, `eventDelete`,
  and `eventUpdateGeneralSection` (rename), each trialed read-before/read-after
  on the throwaway test enquiry (§13).

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
  2. Subscribe to that table's **data pub** with the ids to receive the
     documents. Tables that define no custom data pub are served by
     aldeed:tabular's built-in `tabular_genericPub(tableName, ids, projection)`
     **[live]** (confirmed for `ServiceList`, delivering into collection
     `services`); try it first. Some tables have a custom pub (e.g.
     `activitiesWithExtra` for `SystemActivities`). Signature varies; try, in
     order, `[tableName, ids, proj]`, `[ids, proj]`, `[tableName, ids]`, `[ids]`
     until the collection fills.
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
pubs: `eventBasicInfo(eventId)` **[live]** (multi-cursor: the event doc, its
venue, and the event's `timelines` doc), `eventNotes(eventId)` **[live]**
(collection `notes`; both pubs are subscribed by the event page rather than from
any statically greppable `subscribe(` call, found by watching the page's WS
frames per §6.4), `eventCustomersInfo(eventId)`, `eventCosts(eventId)`,
`eventTransactions(eventId)` **[live]** (collection `transactions`),
`eventFinancialRecords(eventId)` **[live]** (collection `financial-records`),
`eventTermsAndConditions(eventId)`,
`eventServiceBookings(eventId)`, `eventDocs(eventId, documents)`, `eventLayouts(eventId)`,
`eventTables(eventId)`, `eventMessages(eventId)`, `eventActivities(eventId, limit)`,
`eventActivitiesCount(eventId)`, `guests(eventId)` **[live]** (collection `guests`),
`enquiryData(eventId)`, `tastingBookingsForEvent(eventId)`, `eventPricesAndDrinks(eventId)`.

Lifecycle methods (write):
- `eventCreateEnquiry({doc, calendarEventId?})` **[live]**. The doc is flat
  (EnquiryCreationSchema): required `venueId`, `email`, `firstname`, `lastname`,
  `enquiryData: {date}` (= when the enquiry was made, an EJSON date); optional
  `type`, `reference`, `telephone`, `company`, and in `enquiryData`: `sourceId`,
  `heardAboutUsId`, `dateDesired` (free text), `budget`, `accommodation`.
  firstname/lastname/email become the main customer. Returns the new event id.
  The new event has **no date** until `eventHoldDate`/`eventChangeDate` sets one.
  Schema-validation failures come back as opaque 500s, not Match errors, so
  discover doc shapes from the dynamic-import chunk (§6.4), not by iterating.
- `eventCreateEnquiryWithMessage({venueId, ...})`, `eventCreateConfirmed({customer, event})`
- `eventChangeStatus({eventId, toStatus})` **[live]** (0↔3, 4→0 verified; →2
  Cancelled is a silent no-op: result ok, status unchanged; cancellation needs
  the workflow method), `eventCanChangeStatus({eventId, toStatus})` (read)
  **[live]**: returns null for "no objection", even for nonsensical transitions;
  the real gate is server-side in `eventChangeStatus`.
- `eventChangeDate({eventId, date, eventEndDate?, areaIds?, ceremonyDate?})` **[live]**,
  `eventHoldDate({...same...})` **[live]**. Dates are EJSON; the server
  reinterprets the sent instant's calendar day in the **venue timezone** and
  stores venue-local day bounds (so UTC-midnight in, `date` = local 00:00,
  `endDate` auto-set to local 23:59:59.999; `date_str` renders UTC and can show
  the prior day). `areaIds` is `Match.Maybe([id])`: omitting it works; re-send
  the event's current areas to keep them reserved. change-date keeps the
  status; hold-date sets DateOnHold.
- `eventPreConfirm({eventId, data, transactions, welcomeTemplateId, termsAndConditions, paymentPlanId, timelineId, fileIds})`
- `eventExhaustEnquiry({eventId, doc})` **[live]** (doc keys both optional:
  `{reasonNotBookedId?, venueBookedId?}`; sets status 4 and clears the date),
  `changeEnquiryVenue({eventId, venueId})`
- `eventCancelWithWorkflow({eventId, reasonSlug, note?, cancelFutureCharges, revokePortalAccess})`
  (keys confirmed from the dynamic chunk's validator; **not called**: O-class,
  may stop charges and revoke portal access, see §13),
  `eventCancelBooking({bookingId})`, `eventDelete({eventId})` **[live]**,
  `eventRestore({eventId})` (wire shape accepted; refused for this account,
  which lacks `events.general.to-confirmed-pending`; a deleted event is gone
  for us, so don't delete the test harness mid-build).

Status moves that leave date-holding clear `date` (3→0 releases the held date,
exhaust clears it too); `eventsByDateRange` only returns dated events, so a
date-less enquiry is invisible to `event list`. Find it via the `EventList`
tabular read with an explicit `status` selector (e.g. `{status: {$in: [0..7]},
"customers.firstname": ...}`), which overrides the table's default filter.

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

Guests: named guests are docs in collection `guests` (one per guest, served by the
`guests(eventId)` pub); the headcount (`currentMain`/`currentAdditional` on the
event) is a separate record. Writes:
- `eventAddGuest({eventId, data})` **[live]**: data is EventGuestAddSchema:
  `firstname`, `lastname` (required), `role` (free text), `category`
  (EventGuestCategoryEnum §7), `type` (EventGuestTypeEnum §7), `attendingStatus`
  (EventGuestAttendingStatusEnum §7). The last three carry schema defaults
  (Main, Adult, Yes) but `validate()` runs without clean, so crude sends them
  explicitly (omission untried). Returns the new guest id. Adding an attending
  guest auto-increments the matching headcount type; deleting does not decrement.
- `eventUpdateGuest({eventId, guestId, modifier})` **[live]**: Mongo modifier over
  EventGuestCoreSchema fields (the add fields plus title, middleNames,
  preferredName, dateOfBirth, nationality, email, phone, landline, address,
  responsibleGuestId, partnerIndex, specialRequirements, notes, allergies,
  dietaryRestriction, airborneSensitivity, menuChoice, tasks).
- `eventDeleteGuest({eventId, guestId})` **[live]**, `eventDeleteGuests({eventId, guestIds})`,
  `eventImportGuests({eventId, entries, detail})`.
- `eventUpdateGuestNumbers({eventId, modifier})` **[live]**: Mongo modifier over
  `{currentMain: {adults, teenagers, children, infants, suppliers}}` (integers ≥0;
  `currentAdditional` likewise, valid only when `config.allowAdditionalGuests`),
  e.g. `{$set: {"currentMain.adults": 80}}`. A count below the named guestlist's
  total for that type is refused: the method **returns** `{error: ...}` as its
  result (no DDP error) and applies nothing.
- `eventAssignGuestAttendances({eventId, guestIds, status})`, `eventAssignGuestChoices({eventId, guestIds})`.

Notes: one doc per note in collection `notes` (NoteSchema: `sectionId` slug §7,
`author` (set server-side to the staff name), `text`, `eventId`, `venueId`,
`tenantId`, audit fields), served per event by `eventNotes(eventId)` **[live]**
and tenant-wide by the `NotesList` tabular (default selector: current tenant,
`deleted != true`). Writes:
- `eventAddNote({eventId, text, sectionId?})` **[live]**: returns the note id;
  omitted sectionId defaults server-side to `notes`; an explicit slug
  (e.g. `general`) is stored as sent.
- `eventUpdateNote({noteId, text, calendarEventId?})` **[live]** (text
  replacement verified; calendarEventId untried), `eventRemoveNote({noteId})` **[live]**.

Timeline: an event's entries live in one doc per event in collection `timelines`
(`{eventId, venueId, tenantId, entries: [...]}`), created lazily by the first
entry write and kept (empty) after the last entry's deletion; tenant timeline
templates are eventId-less docs in the same collection. The bare `timelines` pub
**[live]** serves the templates name-only (no entries field); the event's doc
arrives with full entries via `eventBasicInfo(eventId)` **[live]**; a single
template with entries via `timeline(timelineId)` **[bundle]**. Entry shape
(TimelineEntryCreateSchema): `type` (TimelineEntryTypeEnum §7), `time` (EJSON,
required when Absolute; stored as sent, no venue-timezone rewriting),
`timeRefId` (required when Relative; the constant `c3r3mnyT7m3L7n355` anchors
RelativeToCeremony), `relOffsetMinutes` (required when Relative or
RelativeToCeremony, negative = before), `durationMinutes?`, `description`
(required), `notes?` (HTML), `readOnly?`, `staffOnly?`, `sectionId` (slug §7,
defaults to `timeline`). Writes:
- `eventAddNewTimelineEntry({eventId, entry})` **[live]**: returns the entry id
  (absolute and relative forms both verified).
- `eventEditTimelineEntry({eventId, entryId, entry})` **[live]**: entry is a
  full replacement document, not a Mongo modifier.
- `eventDeleteTimelineEntry({eventId, timelineEntryId})` **[live]**: reports
  success for unknown entry ids too, so read after deleting.
- `eventImportTimeline({eventId, timelineId})` **[live]**: appends the
  template's entries to the event under fresh entry ids; revert = delete them
  one by one.

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

Data pubs: reach for `tabular_genericPub` first (§5); hunt for a custom pub only
if it delivers nothing. Per-table data-pub definitions are not minable from the
static bundle: the `<hash>.js?meteor_js_resource=true` script named in the page
source is only Meteor's loader/vendor layer (~1.3 MB **[live]**), and table,
method, and schema definitions arrive in dynamic-import modules at runtime.

Those dynamic modules are themselves plain unauthenticated
`<hash>.js?meteor_js_resource=true` URLs **[live]**; one ~15 MB chunk carries
the method validators and the SimpleSchema definitions. To get its hash, load
the app in a browser (login not required for the chunk to be listed) and read
the loaded-script URLs (CDP `Debugger.scriptParsed`, or DevTools → Sources),
then curl the chunk and grep it: `name:"<method>",validate` shows a method's
`validate()` body; `<Name>Schema` definitions resolve the composed `doc`
shapes (this is how `eventCreateEnquiry`'s doc was decoded, §6.1). Prefer this
over live iteration for any schema-validated method: those return opaque 500s,
not Match errors. For plain `check()` methods, the live DDP error does name
the failing Match. Watching the real call in the logged-in browser's DevTools
(Network → WS frames) remains the route for observing side effects. To
enumerate method *names* statically, the loader-layer bundle suffices; its
hash changes each release, so read it fresh:

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
`TransactionStatus`: 0 Accepted, 1 Failed, 2 Cancelled, 3 Pending.
`FinancialRecordType`: 1 Proforma, 2 Invoice, 3 CreditNote.
`FinancialRecordStatus`: 1 Valid, 4 Cancelled, 5 Draft.
`PaymentMethod` (transaction `method`): 0 Cash, 1 Card, 2 Cheque, 3 Transfer,
4 DirectDebit, 5 EscrowAccount, 6 OnlineBankTransfer, 100 Other.
`CalendarEventType`: 0 ShowAround, 1 Meeting, 2 Holiday, 3 OpenDay, 5 ItemDelivery,
6 Tasting, 7 Maintenance, 8 PhotoShoot, 9 Accommodation, 10 Ceremony, 11 InternalMeeting, 100 RegularEvent.
`ServiceBooking status`: 1 Pending, 2 Booked, 3 Cancelled.
`AuditLogType` (collection `audit-logs`, field `type`): 1 Insert, 2 Update, 3 Delete.
`EventGuestTypeEnum` (strings): Adult, Teenager, Child, Infant, Supplier; the
matching headcount keys are adults, teenagers, children, infants, suppliers.
`EventGuestCategoryEnum` (strings): Main, Additional.
`EventGuestAttendingStatusEnum`: 0 Yes, 1 No, 2 Maybe.
`TimelineEntryTypeEnum`: 0 Relative, 1 Absolute, 2 RelativeToCeremony (2 exists
but is outside `values()`, so schema validation rejects it on create; the client
rewrites such entries to Relative with timeRefId `c3r3mnyT7m3L7n355`).
`EventSectionEnum` (string slugs, not integers): general, wedding, package,
tasting-date, menu-choice, order, bar, notes, activities, chat, transactions,
documents, guests, overview, costs, timeline, suppliers, items, terms, people,
home, reviews, branding, audit-log, related, layouts, seating-plan, workflows.
Used as `sectionId` in notes, timeline entries, and `eventSetSectionStatus`.

Event document (from `eventsByDateRange`): `status`, `type`, `date`/`endDate`/`ceremonyDate`
(EJSON), `customers:[{firstname, lastname, main, userId}]`, `reference`, `name`,
`venueId`, `tenantId`, `currentMain`/`currentAdditional`/`includedMain`/`includedAdditional`
(`{adults, teenagers, children, infants, suppliers}`), `areaIds`/`reservedAreaIds`,
`enquiryData`, `weddingData`, `config`.

Transaction document (from `eventTransactions`): `kind`, `status`, `type`
(0 Credit, 1 Debit, 2 Escrow), `amount`, `method` (payments only), `dueDate`,
`description`, `sectionId`, `financialRecordId` (once invoiced),
`amountDistribution` (per-category split), `systemGenerated`, audit fields.

Financial-record document (from `eventFinancialRecords`): `type`, `status`,
`reference` (e.g. `INV-HR-000018`), `date`/`dueDate`, `entries` (line items
with `transactionId` and tax/discount breakdown), `subTotals`, `totalAmount`,
`totalPaid`, `clientId`.

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

**Shipped (reference):** `event list [--from --to --status]`, `event get <id>`,
the lifecycle verbs: `event create-enquiry`, `change-status <id> <status>`,
`change-date <id> --date`, `hold-date`, `exhaust-enquiry`, `rename <id> --name`
(→ `eventUpdateGeneralSection`), `delete`, `restore` (permission-gated),
`cancel` (→ `eventCancelWithWorkflow`, unverified, see §13); plus the per-event
sub-apps `guest` (`list`, `add`, `update`, `delete`, `set-numbers`), `timeline`
(`list`, `add`, `update`, `delete`, `import`), and `note` (`list`, `add`,
`edit`, `delete`), each verb trialed on the test enquiry (§6.1 markers); and the
finance reads `transaction list <eventId>` and `invoice list <eventId>` /
`get <eventId> <recordId>` (financial records), verified against a live event's
finance data.

**T1, operational (read + write):**
- `transaction` writes: `charge`, `payment`, `refund`, `discount`, `approve`, `cancel`.
- `invoice` (financial-record) writes: `pdf`.
- `service-booking`: `list <eventId>`, `add`, `confirm`, `cancel`.
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
(`event cancel`, `event delete`) prompt for confirmation unless `--yes`, as
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

**Write-testing policy.** Live-trial a write only when certain it is safe and
reversible. The standard harness is a throwaway test enquiry, named so staff
recognise it as a test (e.g. "CRUDE TEST (ignore)"), created via
`eventCreateEnquiry` and deleted at build end. Trial protocol per method:
read-before → call → read-after → revert → read-again. Before the first call of
an uncertain method, watch the real action's WS frames in the logged-in browser
(read-only) to learn the payload and spot side effects (mail templates, finance
documents). When unsure whether a call is destructive or externally visible
(sends mail, touches finance/Xero, alters contract state), do not call it: ship
the verb with its payload accepted but marked unverified in `--help`, keep its
[bundle] marker in §6, and list it in the verification summary at the end of
the build.

A standing test enquiry exists while the build is in progress: "CRUDE TEST
(ignore)", id `xgxeKKgYdNZmRZHGR`, status Enquiry, date 2031-11-20, main
customer alice@example.com. Later resource commits trial guest/timeline/note
verbs on it. Keep it until the build ends: `eventRestore` is permission-gated
for this account (§6.1), so deleting it is one-way and a replacement means a
fresh `event create-enquiry`.

- Method args behind composed/external validators (still open:
  `calendarEventCreate`'s `doc`, `paymentPlanCreate`, the nested CLI payloads in
  §9 such as a charge's `doc`) are not destructured in the loader bundle.
  Resolve them from the dynamic-import chunk's schema definitions (§6.4, the
  route that decoded `eventCreateEnquiry`'s doc, the guest `data`, and the
  timeline `entry`); schema-validation failures on the wire are opaque 500s, so
  blind live iteration does not converge. Watching the real call's WS frames in
  the logged-in browser also works and is the only way to see side effects.
- `eventCancelWithWorkflow` stays uncalled (O-class: the name says it runs the
  cancellation workflow, which may cancel future charges, revoke portal access,
  and plausibly send mail). Plain `eventChangeStatus` to Cancelled is a silent
  no-op, so there is no harmless path to a cancelled state. To verify: observe
  a real cancellation's WS frames and check for mail/finance calls.
- `eventRestore` is refused for this account (`events.general.to-confirmed-pending`);
  its effect (and what status a restored event lands in) is unverified.
- T3 catalog reads go through `tabular_genericPub` (§6.4); write-method args
  remain unconfirmed.
- Tabular data-pub signatures vary per table; use the try-in-order approach in §5.
