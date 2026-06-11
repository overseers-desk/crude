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
confirmed; **[bundle]** = read from the minified client bundle, not yet run;
**[chunk]** = arg keys and payload schema decoded statically from the
dynamic-import chunk's validators (§6.4), never run.

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
     **[live]** (confirmed for all eight catalog tables; the table-to-collection
     map is in §6.4); try it first. Some tables have a custom pub (e.g.
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
`eventTermsAndConditions(eventId)` **[live]** (collection `terms-and-conditions`),
`eventServiceBookings(eventId)` **[live]** (multi-cursor: collection
`service-bookings` plus the referenced `services` docs),
`eventDocs(eventId, documents)` **[live]** (collection `files`; the second
param is the event doc's `documents` id array, carried by `eventBasicInfo`
but not by `eventsByDateRange`), `eventLayouts(eventId)`,
`eventTables(eventId)`, `eventMessages(eventId)` **[live]** (collection
`messages` plus the attachment `files` docs),
`eventActivities(eventId, limit)` **[live]** (collection `activities`),
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
  `eventDelete({eventId})` **[live]**,
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

Documents (write): `eventAddDoc({docId, fileObj})`, `eventDeleteDoc({docId, fileId})`
**[chunk]** (docId = the file's `containerId`; not called, §13),
`eventChangeDocName({docId, fileId, newName, staffOnly})`, `eventGetDocumentLinks({docId, fileIds})` (read).

Service bookings: one doc per booking in collection `service-bookings`
(`{eventId, serviceId, status, selectedOptions, questionsAndAnswers, from?, to?,
timelineLinkId?}`). Writes:
- `eventAddServiceBooking({eventId, serviceId, selectedOptions, questions})` **[live]**:
  selectedOptions is one or more SelectedOptionSchema objects: the service
  option's `_id`, `name`, `internalName?`, `description`, `price?` (omitted
  price = on quote) plus `quantity` (integer ≥0; ≥1 on at least one option)
  and optional `included`, `guestIds`; `questions` is `{question, answer?}`
  pairs, stored on the doc as `questionsAndAnswers`. Creates the booking at
  status 1 Pending with no transaction or timeline side effect (trialed on the
  harness; every service in this tenant is in-house, `supplierId` unset, so no
  supplier notification was possible). Works on an enquiry-status event even
  though the UI shows no Suppliers section for enquiries.
- `eventEditServiceBooking({eventId, bookingId, selectedOptions, questions})` **[live]**:
  selectedOptions is a full replacement array.
- `eventCancelServiceBooking({eventId, bookingId})` **[live]**: sets status 3
  and keeps the doc. No booking delete method exists, so a cancelled booking
  is a permanent record (list UIs hide cancelled bookings behind a "Show
  Cancelled" toggle).
- `eventConfirmServiceBooking({eventId, bookingId})` (**not called**, O-class:
  confirming is the step most likely to notify a supplier and raise the
  service's deposit charge; the effect is server-side and was unobservable on
  this supplier-less tenant).

Finance (write; all **[chunk]**, none called: finance/Xero coupling, §13). The
transaction-create docs share a base: `amount` (≥ 0, required), `dueDate`
(EJSON date, required), `description?`:
- `makeChargeTransaction({eventId, doc})`: doc (CreateChargeSchema) adds
  `categoryId?` (a `charges`-tag category id) and `sectionId?` (slug from the
  manual-charge subset: general, wedding, tasting-date, menu-choice, order,
  bar, transactions, guests, suppliers; required once categoryId is set).
- `makeDiscountTransaction({eventId, doc})`: doc is the base alone.
- `makeRefundTransaction({eventId, doc})`: doc adds required `method`
  (PaymentMethod §7) and `financialRecordId`.
- `makeSecurityDepositTransaction({eventId, doc})`: doc adds required
  `categoryId` and `sectionId`.
- `createPaymentTransaction({eventId, financialRecordId, method, amount,
  description?})`: flat typed args, no doc; description is the one optional.
- `createCreditNote({eventId, doc})`: doc is `{entries: [{_id,
  amountToCredit ≥ 0}], financialRecordId, date, dueDate ≥ date}`.
- `approveTransaction({transactionId})`; `cancelTransaction({transactionId})`
  (the server refuses non-cancellable ones; cancelling a payment or refund
  needs the void-credit permission); `reviseTransaction({transactionId,
  modifier})` (modifier over dueDate/description/amount, `$set.amount` > 0);
  `generateFinancialRecordDocument({financialRecordId})` (the artifact is
  visible in the customer portal); `toggleEventSkipXeroJournal({eventId})`;
  `paymentPlanCreate`; `paymentPlanUpdate({docId, modifier})`;
  `paymentPlanDelete({docId})`.

Terms (write): `termsCreate({doc})`, `termsDelete({termsId})`,
`termsAcceptPending({eventId})` **[chunk]** (accepts every pending terms record
on the event: contract state, not called, §13), `termsAnswer({termsId, answer})`,
`termsGeneratePDF({termsId})` **[chunk]**.

Messaging (write): `eventCreateDraftMessage({eventId})`, `eventUpdateDraftMessage({messageId, message})`,
`eventSaveMessage({messageId, message})`, `eventSendEmailTemplate({templateId, eventId, userId})`
**[chunk]** (sends real mail to the customer, not called, §13),
`eventMarkMessageAsOpened({eventId, messageId})`.

Activities: one doc per entry in collection `activities` (readable `text`,
`section`, integer `type` code, `verifiedById`/`verifiedDate`), served by
`eventActivities(eventId, limit)`. An actor's own activities arrive already
verified, so a flip false→true was not demonstrable on the harness. Writes:
`eventAddCalledClientActivity({eventId, noteText?})`,
`eventVerifyActivity({activityId})` **[live]** (re-stamps `verifiedDate`),
`eventVerifyAllActivities({eventId})` **[live]** (accepted; targets unverified
entries), `tenantVerifyActivities(...)`. Tenant-wide reads: the
`SystemActivities` table → `activitiesWithExtra` data pub; the selector for
unverified activities is `{verifiedById: null}`.

Export: `exportEvents({clientSelector, extension, mode})`, `tenantImportEvents({entries, detail})`.

### 6.2 Availability & bookings (T2)

Publications: `availabilityByDateRange(from, to)` **[live]** (collection
`availability`, plus the range's `calendar-events`), `calendarEventsByDateRange(from, to)`
**[live]** (collection `calendar-events`, plus the linked `events`),
`calendarEvent(calendarEventId)` **[live]** (the doc plus its `activities`),
`tastingEventsByDateRange(from, to)` **[live]** (collection `tasting-events`;
this tenant has none, so the doc shape is from TastingEventSchema only),
`calendarEventsByDateRangeForAvailability(from, to, venueIds, availabilityTypes)`,
`tastingBookingsForEvent(eventId)`, `tastingBookingsByTasting(tastingEventId)`.

Appointments (collection `calendar-events`):
- `calendarEventCreate(doc)` **[live]**: the arg is the flat
  CalendarEventCreateSchema doc, no wrapper (idSource `venueId`). Required
  `venueId`, `type` (CalendarEventTypeEnum §7), `start` (EJSON); optional `end`
  (≥ start + 15 min when set), `title`, `staffId` (1–2 user ids), `eventId`,
  `allDay`, `weatherType`, `attended`, `attendants`. Returns the new id. The
  schema has no notification field; reminder mail belongs to the customer
  appointment types (`sendsRemindersTypes()`: ShowAround, Meeting, ItemDelivery,
  CustomAppointment1–3) and to the separate `eventCreateCalendarEvent({eventId,
  data})` path, whose simplified schema carries an `emailTemplateId`. Trialed as
  an InternalMeeting with no event link, the plain staff-calendar shape.
- `calendarEventUpdate({id, modifier})` **[live]**: Mongo modifier over
  CalendarEventSchema fields, but the server needs `$set.start` and `$set.end`
  together in every modifier; a title-only or start-only `$set` passes schema
  validation and then fails with an opaque `method-exec-err`.
- `calendarEventDelete({id})` **[live]**,
  `calendarEventSetAttendance({calendarEventId, eventId, attended, noteText})`,
  `eventCreateCalendarEvent({eventId, data})`, `eventUpdateCalendarEvent({calendarEventId, modifier})`,
  `eventDeleteCalendarEvent({calendarEventId})`, `eventCancelAppointment({calendarEventId, eventId})`.

Availability windows (collection `availability`): recurring bookable-slot
definitions the public appointment-booking widget offers, not internal date
blocks (a block is an `exceptions` entry inside a window). **Not called** for
that reason (O-class, public-widget visibility); shapes from the chunk:
- `createAvailability({doc})`: doc is AvailabilityCoreSchema: `title`,
  `availableFor` (CalendarEventTypeEnum `availabilityValues()`: ShowAround,
  Meeting, ItemDelivery, CustomAppointment1–3, Ceremony), `from`/`to` (EJSON,
  to > from + 30 min), `defaultStaffId`, `availability` (array of `{day`
  (DaysEnum: 1–7 Mon–Sun, 10 Weekdays, 11 Weekends, 12 EveryDay), `start`
  "HH:MM", `end?` "HH:MM", `slotDuration` ≥15, `bufferBetweenSlots` 0–720,
  `bookingsPerSlot` 1–999, `onlyEventTypes?`, `exceptEventTypes?}`),
  `exceptions?` (array of `{start, end, title?}` EJSON spans), `venueId`,
  `minTimeBeforeBooking` 0–999.
- `updateAvailability({availabilityId, modifier})`: modifier over
  AvailabilityUpdateSchema (the core minus venueId/availableFor/defaultStaffId);
  exceptions must fall inside `$set.from`..`$set.to`.
- `deleteAvailability({availabilityId})`.
- `getVenueAvailability({venueId, eventId, startDate, endDate, eventType})` (read).

Tastings: tasting events are venue-hosted slots (TastingEventCoreSchema: `type`,
`venueId`, `permittedVenueIds?`, `staffOnly?`, `permittedProductMenuIds?`,
`startTime`, `timeInterval` 1–300, `capacityPerSlot`, ...); bookings put an
event's couple into a slot. **Not called** (the server side may mail the couple,
and the booking's optional `transactionId` hints finance coupling); shapes from
the chunk:
- `eventAddTastingBooking({previousBookingId?, booking})`: booking is
  TastingBookingNHSchema: required `tastingEventId`, `tastingSlot` (integer slot
  index), `eventId`, `foodToTaste` (string array), `numberAttending`; optional
  `wineToTaste`, `transactionId`, `notes` (`{subType, text}` array), `attended`,
  `dietaryRestriction`, `allergies`, `airborneSensitivity`, `bookingNotes`.
- `eventCancelBooking({bookingId})`: the tasting-booking cancel, despite the
  event-sounding name (it lives in tasting-bookings/methods.ts, zone
  TastingBookings).
- `tastingEventCreate({doc})`, `tastingEventUpdate({tastingEventId, mod})`,
  `tastingEventDelete({tastingEventId})`,
  `tastingEventSetAttendance({tastingEventId, tastingBookingId, attended})`.

Calendar plumbing: `tenantUpdateCalendars({modifier})`,
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

Reports: `reportsBasicInfo` **[live]** (no params; collection `reports`, fields
`name` and `type` only) and `report(reportId)` **[live]** (the full definition
with its `queryLines`) drive `report list`/`get`; `reports(venueId)` (pub);
`reportCreate({doc})`, `reportUpdate({docId, modifier})`, `reportClone({docId})`,
`reportDelete({docId})`, `reportGenerate({reportId})`. Report types include
`SalesFunnel` and `EventMarketing`.

### 6.4 Catalog & config (T3): reads live, write-method args to confirm

The eight catalog tables behind `crude-sonas <resource> list|get` are all served
by `tabular_genericPub` **[live]**, signature `[tableName, ids, projection]`,
delivering into these collections:

| table | collection | | table | collection |
|---|---|---|---|---|
| `SuppliersList` | `suppliers` | | `TemplatesList` | `templates` |
| `ServiceList` | `services` | | `CategoriesList` | `categories` |
| `DrinksList` | `drinks` | | `VenueList` | `venues` |
| `PackageList` | `price-lists` | | `UserList` | `users` |

All eight declare `searching: false` in their tabular definitions and ignore
`tabular_getInfo`'s searchTerm (confirmed live: counts stay unfiltered; on
`EventList` the same term does change the counts), so crude's `--search`
filters the fetched rows client-side. `get <id>` is the same two-step with
selector `{_id: ...}`; pass the collection name explicitly, since the
new-docs-only auto-detect misses documents already in the store (the
logged-in user's own `users` doc).

Other tables (not yet built): `FormsList`, `UserRoleList`, `TransactionList`
(`transactionsWithEventDate`), `FinancialRecordsList` (`financialRecordsWithEventDate`),
`Inbox` (`messagesWithExtra`), `ReviewList`, `WorkflowsList`, `AuditLogList`
(`auditLogComposite`, collection `audit-logs`). For these, reach for
`tabular_genericPub` first (§5); hunt for a custom pub only if it delivers
nothing. Per-table data-pub definitions are not minable from the static bundle:
the `<hash>.js?meteor_js_resource=true` script named in the page source is only
Meteor's loader/vendor layer (~1.3 MB **[live]**), and table, method, and schema
definitions arrive in dynamic-import modules at runtime.

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
6 Tasting, 7 Maintenance, 8 PhotoShoot, 9 Accommodation, 10 Ceremony,
11 InternalMeeting, 12–14 CustomAppointment1–3, 100 RegularEvent. Subsets:
`sendsRemindersTypes()` = {0, 1, 5, 12, 13, 14} (the customer appointment kinds);
`hasAttendees()` = {3}; `createOnCalendarTypes()` (staff-created entries) =
{2, 3, 7, 8, 9, 11}; `availabilityValues()` = {0, 1, 5, 10, 12, 13, 14}.
`MessageStatus`: 0 Incoming, 1 Received, 2 Outgoing, 3 Sent, 4 Delivered, 7 Opened, 9 Draft.
`MessageTransport`: 0 Internal, 1 Email.
`TermsAndConditionsStatus`: 0 Waiting, 1 Accepted, 2 Rejected.
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

Message document (from `eventMessages`): `status`, `transport`, `author`,
`subject`, `bodyPreview`, `sender`/`recipient`/`otherRecipients`, `inReplyToId`,
`attachments` (file ids), `log`, `externalId`, `categories`, `tags`.

Terms record (from `eventTermsAndConditions`): `name`, `required`, `status`,
`category`, `type`, `channel`, `answeredAt`/`answeredBy`/`answeredByName`.

File document (from `eventDocs` and `eventMessages`): `name`/`displayName`,
`type` (the container kind, e.g. `messages`, `documents`), `contentType`,
`size`, `containerId`, `references`, `status`.

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
finance data; `service-booking` (`list`, `add`, `edit`, `cancel` trialed on
the harness; `confirm` ships unverified, §6.1); the per-event reads
`message list`, `document list`, `terms list` (verified on a live event); and
`activity` (`list`, `verify`, `verify-all`, trialed on the harness's own
activities); the T2 scheduling sub-apps: `appointment` (`list`, `get`,
`create`, `update`, `delete`, the lifecycle trialed on a throwaway
InternalMeeting, §6.2), `availability` (`list` verified; `create`, `update`,
`delete` ship unverified: windows feed the public booking widget, §6.2), and
`tasting` (`list` verified on the empty tenant; `book`, `cancel` ship
unverified: the server side may mail the couple, §6.2); and the T3 catalog
reads: `supplier`, `service`, `drinks-package`, `package`, `template`,
`category`, `venue`, `user`, each `list [--limit --search --json]` and
`get <id>` via the factory-made sub-apps over the §6.4 table map, plus
`report list`/`get` over the §6.3 report pubs (every list and several gets
verified live); and the C-class write verbs, shipped uncalled per §13
(finance/Xero coupling, real mail, contract state), payloads chunk-decoded
(§6.1 **[chunk]** markers): `transaction charge|payment|refund|discount`,
`transaction approve|cancel`, `invoice pdf`, `message send`,
`document delete`, `terms accept|pdf`.

**Skip / defer:** `document add` (`eventAddDoc`: the fileObj presumably comes
from the §2 upload sidecar, unexplored), reviews, platform-contracts,
workflows, forms.

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
customer alice@example.com. Resource commits trial their write verbs on it.
Keep it until the build ends: `eventRestore` is permission-gated for this
account (§6.1), so deleting it is one-way and a replacement means a fresh
`event create-enquiry`. It carries one cancelled service booking (the
service-booking trial's residue; cancelled bookings cannot be deleted, §6.1)
and the activity records of the trials; both are invisible in its UI view.

- Method args behind composed/external validators (still open:
  `paymentPlanCreate`) are not destructured in the loader bundle.
  Resolve them from the dynamic-import chunk's schema definitions (§6.4, the
  route that decoded `eventCreateEnquiry`'s doc, the guest `data`, the
  timeline `entry`, and the §6.1 transaction docs); schema-validation failures
  on the wire are opaque 500s, so blind live iteration does not converge.
  Watching the real call's WS frames in the logged-in browser also works and
  is the only way to see side effects.
- The finance, mail, and terms verbs (`transaction charge|payment|refund|discount`,
  `transaction approve|cancel`, `invoice pdf`, `message send`,
  `document delete`, `terms accept|pdf`) ship **[chunk]**:
  payload shapes decoded statically, never invoked (finance/Xero coupling,
  real mail, contract state). To verify: observe the real UI actions' WS
  frames.
- `eventCancelWithWorkflow` stays uncalled (O-class: the name says it runs the
  cancellation workflow, which may cancel future charges, revoke portal access,
  and plausibly send mail). Plain `eventChangeStatus` to Cancelled is a silent
  no-op, so there is no harmless path to a cancelled state. To verify: observe
  a real cancellation's WS frames and check for mail/finance calls.
- `eventRestore` is refused for this account (`events.general.to-confirmed-pending`);
  its effect (and what status a restored event lands in) is unverified.
- Availability writes (`createAvailability`, `updateAvailability`,
  `deleteAvailability`) and tasting bookings (`eventAddTastingBooking`,
  `eventCancelBooking`) ship unverified (§6.2): windows surface on the public
  booking widget, and a tasting booking may mail the couple. To verify: observe
  the real UI actions' WS frames.
- T3 catalog reads are live (§6.4); the catalog write-method args remain
  unconfirmed.
- Tabular data-pub signatures vary per table; use the try-in-order approach in §5.
