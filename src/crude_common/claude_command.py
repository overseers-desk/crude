"""Install and keep current the crude command for Claude Code.

This installs a Claude Code *command* (the commands directory of the
configuration tree the session reads (``CLAUDE_CONFIG_DIR`` when set,
``~/.claude`` otherwise)), not a
skill. A user's skills directory is frequently a version-controlled, curated
collection, so a CLI writing into it would pollute that repository; the commands
directory is the conventional home for a tool to register itself. The ``COMMAND``
text below is the single source for the command's content. Each site CLI keeps
the installed file equal to it: on every run, when the file is missing or differs
from ``COMMAND``, it is rewritten. "Current" means byte-for-byte equal to
``COMMAND``, so there is no version stamp to maintain and no per-release judgement
about whether the command changed. A same-named skill, if the user keeps one,
supersedes the command and the refresh leaves it alone.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer

from crude_common import asof, version as crude_version
from crude_common.config import set_account

COMMAND_NAME = "crude"

VERSION_HELP = "Show the crude version and exit."

ACCOUNT_HELP = (
    "Select a named account from this site's config (a [site.<name>] subtable); "
    "omit for the site's default account."
)

# The command body and the single source of its content. The description lists
# the sites crude supports and stays short, so an agent reaches for it when those
# sites come up; how to drive the CLIs is the body below, not the description.
COMMAND = """---
name: crude
description: Read and edit your own data on atdw-online.com.au (ATDW tourism listings), australia.skal.org (Skal Australia member portal), rezdy.com (products, availability, bookings), deputy.com (rostering, timesheets, leave, employees), app.sonas.events (Sonas wedding-venue events), xero.com (Xero accounting), airwallex.com (Airwallex payments and transactions), clover.com (Clover POS orders and catalog), graph.facebook.com (Facebook Pages: posts, insights, comments), wise.com (Wise Business balances, statements, transfers, recipients), and a self-hosted Mautic instance (marketing forms and who submitted them, contacts, segments, campaigns, email send counts).
allowed-tools: Bash
---

# crude

crude provides command-line clients for reading and editing your own data on a handful of sites, each through one `crude-<site> <resource> <verb>` grammar. Some sites lack a usable public API and are reached through their internal endpoints; others ride a documented one. Each site is its own binary. Configuration for all of them lives in `~/.config/crude/config.toml` (sections `[atdw]`, `[skal]`, `[rezdy]`, `[deputy]`, `[sonas]`, `[xero]`, `[airwallex]`, `[clover]`, `[facebook]`, `[mautic]`, `[wise]`). Add `--json` to any read command for machine-readable output.

A site can hold several accounts. The bare `[site]` section is the default account; a `[site.<name>]` subtable is a named one. Select it with `--account/-a <name>` before the resource (or `$CRUDE_ACCOUNT`), e.g. `crude-rezdy --account es booking cancellations --from 2026-05-03`. Without `--account`, the default account is used.

If the `WORLD_AS_OF` environment variable is set (an ISO-8601 instant with timezone), every crude binary bounds its reads to that instant — records created after it are excluded, mutated-after records are flagged (`"_world_as_of"` in `--json`), now-valued reads (current balances, FX rates, live availability, insights) refuse — and **every write verb refuses**. An unparseable or timezone-naive value aborts the command. The per-backend boundary table is in the crude repo docs/manual.md.

## crude-atdw (atdw-online.com.au)

Tourism listings. Credentials in `[atdw]`; the JWT token is cached and renewed automatically.

    crude-atdw login
    crude-atdw listing list [--scope own|all] [--type] [--city] [--state] [--status] [--name] [--limit] [--offset]
    crude-atdw listing get <id>
    crude-atdw listing create (--data '<json>' | -f <file> | stdin) [--yes]
    crude-atdw listing update <id> <field> <value>
    crude-atdw listing submit <id>

`listing list` with no filters returns your own organisation's listings; any filter flag or `--scope all` searches every visible listing. `listing create` takes a full listing object (ATDW requires at least listingType, category, name, and physicalAddress; owningOrganisation defaults to yours); the new listing starts as a draft and is not distributed until `listing submit`. `listing update` PATCHes a single field.

## crude-skal (australia.skal.org)

Skal Australia member portal. Credentials in `[skal]`; the session cookie lasts about 30 days and is cached automatically.

    crude-skal login
    crude-skal member list [--name] [--city] [--club <id>] [--email] [--state] [--limit] [--offset]
    crude-skal member get <id>
    crude-skal club list
    crude-skal event list [--limit]
    crude-skal benefit list [--limit] [--offset]
    crude-skal benefit get <id>

Member `--state` values: active, draft, unpaid, done, club_change (default excludes done). Club IDs: 330 Melbourne, 334 Sydney, 322 Brisbane, 333 Perth, 321 Adelaide, 1003 Gold Coast (full list in the crude repo docs/skal-api.md). `benefit` lists the global Skål International benefits register (worldwide offers); Australian clubs publish their own member offers on a website page, not in this model.

## crude-rezdy (rezdy.com)

Rezdy Supplier API — full CRUD over products, availability, bookings, customers, extras, and pickup lists, plus category/rate/resource assignment and manifest check-in. API key in `[rezdy]` (`api_key`, required `timezone` as an IANA name, optional `environment`); there is no login step. rezdy reads every typed date as the account's operational day, so any command errors if `timezone` is missing.

    crude-rezdy product list [--search] [--limit] [--offset]
    crude-rezdy product get <code>
    crude-rezdy product create (--data '<json>' | -f <file> | stdin) [--yes]
    crude-rezdy product update <code> [--name <s>] [--terms <s>] [--data '<json>'] [--yes]
    crude-rezdy product delete <code> [--yes]
    crude-rezdy product image-add <code> (--data | -f | stdin) [--yes] ; product image-remove <code> <imageId> [--yes] ; product pickups <code>
    crude-rezdy availability list --product <code> --from "<YYYY-MM-DD HH:mm:ss>" --to "<...>" [--min-availability] [--limit] [--offset]
    crude-rezdy availability create (--data | -f | stdin) [--yes]
    crude-rezdy availability update --product <code> --start-local "<YYYY-MM-DD HH:mm:ss>" (--data | -f | stdin) [--yes]
    crude-rezdy availability delete --product <code> --start-local "<...>" [--yes]
    crude-rezdy availability batch (--data | -f | stdin) [--yes]
    crude-rezdy booking list [--status] [--search] [--product <code> ...] [--source-channel <agent code>] [--reseller-reference <ref>] [--role] [--from] [--to] [--created-from] [--created-to] [--updated-from] [--updated-to] [--limit] [--offset] [--all]
    crude-rezdy booking cancellations [--from <YYYY-MM-DD>] [--to <YYYY-MM-DD>] [--limit] [--all]
    crude-rezdy booking get <orderno>
    crude-rezdy booking quote (--data | -f | stdin)
    crude-rezdy booking create (--data | -f | stdin) [--notify] [--yes]
    crude-rezdy booking update <orderno> (--data | -f | stdin) [--yes]
    crude-rezdy booking cancel <orderno> [--yes]
    crude-rezdy customer list [--search] [--limit] [--offset] ; customer get <id> ; customer create (--data | -f | stdin) [--yes] ; customer delete <id> [--yes]
    crude-rezdy extra list [--search] ; extra get <id> ; extra create (--data | -f | stdin) [--yes] ; extra update <id> [--name <s>] [--data '<json>'] [--yes] ; extra delete <id> [--yes]
    crude-rezdy pickup-list list [--search] ; pickup-list get <id> ; pickup-list create (--data | -f | stdin) [--yes] ; pickup-list update <id> [--name <s>] [--data '<json>'] [--yes] ; pickup-list delete <id> [--yes]
    crude-rezdy category list [--search] [--visible | --private] ; category get <id> ; category products <id> ; category add-product <categoryId> <code> [--yes] ; category remove-product <categoryId> <code> [--yes]
    crude-rezdy rate list [--name <s>] [--product <code>] ; rate get <id> ; rate add-product <rateId> <code> [--yes] ; rate remove-product <rateId> <code> [--yes]
    crude-rezdy resource list ; resource sessions <id> [--from] [--to] [--limit] [--offset] ; resource for-session (--session <id> | --product <code> --start/--start-local <t>) ; resource add-session <resourceId> <sessionId> [--yes] ; resource remove-session <resourceId> <sessionId> [--yes]
    crude-rezdy manifest {order,session}-{status,set,remove} --product <code> [--order <no>] [--start <utc> | --start-local <t>] [--checkin/--no-checkin] [--yes]
    crude-rezdy voucher list [--search] ; voucher get <code> ; company get <alias> ; company find <name>

Writes take a JSON body from `--data '<json>'`, `-f <file>`, or stdin, and verbs that create or destroy prompt unless `--yes`; `--json` on any verb returns the raw API object. `product`, `extra`, and `pickup-list` `update` are read-merge-write — a typed flag or `--data` key overlays the fetched object and an empty string clears a field, so `product update P1 --terms "..."` touches only the terms; `availability update` and `booking update` send the body as-is. `booking create` sends `sendNotifications=false` (emails no one) unless `--notify`.

Product **terms** (`--terms`) and **custom booking questions** are product fields edited through `product update`: booking questions are the product's `bookingFields` array (set via `--data`), and because that list is replaced wholesale you send the complete set. Resources are read + session-assignment only — a new resource is created in the Rezdy dashboard, then assigned here; vouchers/coupons are read-only. `product create` enforces a few fields the spec does not flag: `description` ≥100 chars, `durationMinutes`, and a priceOption `id` (pass `0`, rezdy assigns the real one). Full command surface, JSON body shapes, and these specifics are in the crude repo docs/rezdy.md.

`booking cancellations` filters by when the cancellation occurred (dateUpdated), not the session date. Use --from/--to with YYYY-MM-DD dates.
--updated-from / --updated-to on `booking list` apply the same filter to any status.
Every date typed on `booking list` and `booking cancellations` is the account's operational day (the required `timezone`), converted to the UTC instants Rezdy compares against, so a boundary date is not off by one; a time typed without an offset is account-local too.
The lower update bound is applied by Rezdy and the upper one on the pages fetched, so add --all when --updated-to (or the cancellations --to) is used alone; --all fetches all pages (default limit is otherwise applied). A never-updated booking is in no update window.
`booking list --search` is a slow prefix match on customer names and payment or voucher codes. Rezdy has deprecated finding an order number or an agent code through it: use `booking get <orderno>` for an order and `--source-channel <agent code>` for an agent's bookings. `--product` repeats for several products.
`resource sessions --from/--to` is a local-time window (a bare date is the whole day); Rezdy returns the sessions that both start and end inside it, at most 100 a page.
For one day's bookings, give the day as both bounds: `booking list --from 2026-05-25 --to 2026-05-25`. Availability times are local (`YYYY-MM-DD HH:mm:ss`).

## crude-deputy (deputy.com)

Deputy rostering, timesheets, leave. Permanent token in `[deputy]` (`deputy_api_token`, `deputy_install`, `deputy_geo`); there is no login step.

    crude-deputy me
    crude-deputy employee list [--limit] [--all]
    crude-deputy employee get <id>
    crude-deputy roster list [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--area <id>] [--employee <id>] [--limit] [--all]
    crude-deputy area list          # area == OperationalUnit
    crude-deputy timesheet list [--from] [--to] [--employee <id>] [--all]
    crude-deputy leave list [--employee <id>] [--all]

Deputy's Resource API is uniform across ~60 objects, so any object is reachable generically:

    crude-deputy resource list <Object> [--limit] [--start] [--all]
    crude-deputy resource get <Object> <id>
    crude-deputy resource query <Object> [--where "Field op value" ...] [--sort "Field:asc|desc"] [--join Name ...] [--max] [--all]
    crude-deputy resource info <Object>
    crude-deputy resource create <Object> (--data '<json>' | -f file | stdin)
    crude-deputy resource update <Object> <id> (--data '<json>' | -f file | stdin)
    crude-deputy resource delete <Object> <id> [--yes]

`<Object>` is the literal Deputy object name (Employee, Roster, OperationalUnit, Timesheet, Leave, Memo, ...); there is no list-all endpoint, so consult Deputy's docs or use `resource <Object> info` to discover fields. --where operators: eq ne gt ge lt le lk nk in nn is ns (in/nn take a comma-separated value); --json-query passes a full Deputy QUERY body. delete is irreversible and prompts unless --yes.

## crude-sonas (app.sonas.events)

Sonas wedding-venue software. Credentials in `[sonas]` (`username`, `password_hash` = the SHA-256 hex of the password; optional `fingerprint`, `tenant`). Sonas has no public API; crude speaks its Meteor DDP backend directly. The first login from a new machine triggers a one-time device-verification email; open the link once to trust the device (full setup and the protocol are in the crude repo docs/sonas.md).

    crude-sonas event list [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--status <name|number>]
    crude-sonas event leads --source <name|categoryId> [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--status <name|number>] [--list] [--json]   # count enquiries by lead source, by enquiry date
    crude-sonas event get <eventId>
    crude-sonas event create-enquiry --venue <venueId> --email <email> --firstname <name> --lastname <name> [--telephone] [--type <n>] [--date-desired <text>] [--data '<json>']
    crude-sonas event rename <eventId> --name <name>
    crude-sonas event change-status <eventId> <status> [--yes]
    crude-sonas event change-date <eventId> --date YYYY-MM-DD [--end-date YYYY-MM-DD] [--ceremony-date YYYY-MM-DD]
    crude-sonas event hold-date <eventId> --date YYYY-MM-DD [--end-date YYYY-MM-DD] [--ceremony-date YYYY-MM-DD]
    crude-sonas event exhaust-enquiry <eventId> [--data '<json>']
    crude-sonas event delete <eventId> [--yes]
    crude-sonas event restore <eventId>
    crude-sonas event cancel <eventId> --reason <slug> [--note <text>] [--data '<json>'] [--yes]
    crude-sonas guest list <eventId> [--json]
    crude-sonas guest add <eventId> --firstname <name> --lastname <name> [--role <text>] [--category Main|Additional] [--type Adult|Teenager|Child|Infant|Supplier] [--attending 0|1|2] [--data '<json>']
    crude-sonas guest update <eventId> <guestId> --data '<mongo-modifier-json>'
    crude-sonas guest delete <eventId> <guestId> [--yes]
    crude-sonas guest set-numbers <eventId> [--adults N] [--teenagers N] [--children N] [--infants N] [--suppliers N] [--data '<mongo-modifier-json>']
    crude-sonas timeline list <eventId> [--json]
    crude-sonas timeline add <eventId> --description <text> (--time <iso-datetime> | --after <entryId> --offset-minutes N) [--duration N] [--notes <html>] [--section <slug>] [--data '<json>']
    crude-sonas timeline update <eventId> <entryId> --data '<full-entry-json>'
    crude-sonas timeline delete <eventId> <entryId> [--yes]
    crude-sonas timeline import <eventId> <timelineId>
    crude-sonas note list <eventId> [--json]
    crude-sonas note add <eventId> --text <text> [--section <slug>]
    crude-sonas note edit <noteId> --text <text>
    crude-sonas note delete <noteId> [--yes]
    crude-sonas transaction list <eventId> [--json]    # charges, payments, refunds, discounts
    crude-sonas transaction charge <eventId> --data '<doc-json>'       # unverified; touches finance
    crude-sonas transaction payment <eventId> --record <financialRecordId> --method <name|n> --amount N [--description <text>]  # unverified
    crude-sonas transaction refund <eventId> --data '<doc-json>'       # unverified
    crude-sonas transaction discount <eventId> --data '<doc-json>'     # unverified
    crude-sonas transaction approve <transactionId> [--yes]            # unverified
    crude-sonas transaction cancel <transactionId> [--yes]             # unverified
    crude-sonas invoice list <eventId> [--json]        # financial records: proformas, invoices, credit notes
    crude-sonas invoice get <eventId> <recordId> [--json]
    crude-sonas invoice pdf <recordId>                 # unverified; the artifact is portal-visible
    crude-sonas service-booking list <eventId> [--json]
    crude-sonas service-booking add <eventId> --service <serviceId> --option <optionId[:qty]> [--option ...] [--data '<json>']
    crude-sonas service-booking edit <eventId> <bookingId> --option <optionId[:qty]> [--data '<json>']
    crude-sonas service-booking cancel <eventId> <bookingId> [--yes]
    crude-sonas service-booking confirm <eventId> <bookingId> [--yes]  # unverified; may notify the supplier
    crude-sonas message list <eventId> [--json]
    crude-sonas message send <eventId> --template <templateId> --user <userId> [--yes]  # unverified; sends real mail
    crude-sonas document list <eventId> [--json]
    crude-sonas document delete <docId> <fileId> [--yes]               # unverified; docId = the file's containerId
    crude-sonas terms list <eventId> [--json]
    crude-sonas terms accept <eventId> [--yes]                         # unverified; accepts all pending terms (contract state)
    crude-sonas terms pdf <termsId>                                    # unverified
    crude-sonas terms create <eventId> --data '<doc-json>'             # doc: name,text,required,type,category,channel; puts a policy to the couple (decoded, untrialed)
    crude-sonas terms answer <termsId> --answer Accepted|Rejected [--yes]  # alters contract state (decoded, untrialed)
    crude-sonas terms delete <termsId> [--yes]                         # alters contract state (decoded, untrialed)
    crude-sonas activity list <eventId> [--limit N] [--json]
    crude-sonas activity verify <activityId>
    crude-sonas activity verify-all <eventId>
    crude-sonas availability list [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--json]
    crude-sonas availability create --data '<doc-json>'               # unverified; feeds the public booking widget
    crude-sonas availability update <availabilityId> --data '<mongo-modifier-json>'  # unverified
    crude-sonas availability delete <availabilityId> [--yes]          # unverified
    crude-sonas appointment list [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--json]
    crude-sonas appointment get <calendarEventId> [--json]
    crude-sonas appointment create --venue <venueId> --type <name|n> --start <iso-datetime> [--end <iso-datetime>] [--title <text>] [--event <eventId>] [--data '<json>']
    crude-sonas appointment update <calendarEventId> --data '<mongo-modifier-json>'  # $set needs start and end together
    crude-sonas appointment delete <calendarEventId> [--yes]
    crude-sonas tasting list [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--json]
    crude-sonas tasting book --data '<booking-json>' [--previous <bookingId>]  # unverified; may mail the couple
    crude-sonas tasting cancel <bookingId> [--yes]                    # unverified
    crude-sonas <catalog> list [--limit N] [--search <term>] [--json]
    crude-sonas <catalog> get <id> [--json]
    crude-sonas template edit <templateId> [--body-file PATH] [--subject <s>] [--name <s>] [--data '<modifier>'] [--yes]
    # e.g. update the T&C/policy a couple signs (a type-8 template):
    #   crude-sonas template list                          # find the type-8 "...Policy" row in the Type column
    #   crude-sonas template edit <id> --body-file policy.html   # replace the policy body
    #   crude-sonas template edit <id> --subject "Venue Policy 2027"   # or a single field via $set
    crude-sonas report list [--json]
    crude-sonas report get <reportId> [--json]

Event status values: Enquiry, Confirmed, Cancelled, DateOnHold, Exhausted, ConfirmedPending, Completed, Idle. A fresh enquiry has no event date and stays out of `event list` until hold-date or change-date sets one; hold-date also sets DateOnHold, change-date keeps the status. change-status prompts when the target leaves the enquiry group (Enquiry, DateOnHold, Exhausted, Idle); delete and cancel prompt unless --yes. The full resource map (events, finance, guests, timelines, service-bookings, and more) and the remaining subcommand plan live in the crude repo docs/sonas.md.

Named guests (guest list/add/update/delete) and the headcount (guest set-numbers, the currentMain counts shown by event list) are separate records: adding an attending guest auto-increments the matching count, deleting a guest does not decrement it, and set-numbers refuses to go below the named guestlist's total for a type.

Dates typed on any crude-sonas command (`--from`/`--to`, `--date`) are days in the venue's timezone, and dates are shown in it: the config's `timezone` under `[sonas]`, else the top-level one, else the machine's zone.

Timeline entries are absolute (--time, naive ISO counts as UTC) or relative to another entry (--after + --offset-minutes, negative = before); timeline update takes a full replacement entry, not a modifier; timeline import appends a tenant template's entries (template ids are the eventId-less docs in the timelines collection). Note and timeline --section take an EventSectionEnum slug (notes, general, timeline, bar, ...; the table is in the crude repo docs/sonas.md); note add defaults to notes.

service-booking cancel keeps the booking as a Cancelled record (Sonas has no booking delete); edit replaces the whole option list. Option ids come from the service's catalog doc.

The finance, mail, and terms writes ship uncalled (they touch finance/Xero, send real mail, or alter contract state); each says so in --help. The charge/refund/discount --data doc: amount (>= 0) and dueDate (EJSON, {"$date": <epoch-ms>}) required, description optional; refund also needs method (payment-method name maps to a number: Cash 0, Card 1, Cheque 2, Transfer 3, DirectDebit 4, EscrowAccount 5, OnlineBankTransfer 6, Other 100) and financialRecordId; charge also accepts categoryId and sectionId. payment takes flat typed flags instead of --data; terms accept accepts every pending terms record on the event.

Appointment --type takes a name or number: ShowAround, Meeting, Holiday, OpenDay, ItemDelivery, Tasting, Maintenance, PhotoShoot, Accommodation, Ceremony, InternalMeeting, CustomAppointment1-3, RegularEvent. An InternalMeeting with no --event link is a plain staff-calendar entry; the customer appointment types send reminder mail. Commands marked unverified have their payloads decoded but were never trial-called; see docs/sonas.md §6 before relying on them.

`<catalog>` is one of the catalog resources: supplier, service, drinks-package, package, template, category, venue, user. `template list` returns templates of every kind, including the T&C/policy bodies a couple signs (the policy is a venue template, not a per-event record; type 8 on the current Sonas build, the same value for every venue since Sonas is one multi-tenant app). All catalog resources are read-only except `template`, which also has `edit` (templateUpdate; a Mongo modifier over body/subject/name/style). --search matches a case-insensitive substring anywhere in the document.

## crude-xero (xero.com)

Xero accounting over the official OAuth2 APIs. Credentials in `[xero]` (`client_id`, `redirect_uri` = a localhost loopback, `scopes`, plus `client_secret` only when riding a Web-app registration); tokens cache in `~/.config/crude/xero_token.json` and refresh automatically. One Xero login can reach several organisations (tenants); pick one with `--tenant/-t <name|id>` (distinct from `--account`, which picks the connection). Writes need write scopes enabled on the Xero app, then a fresh `crude-xero auth`.

    crude-xero auth [--manual] [--no-browser]      # one-time browser consent
    crude-xero tenants                              # list reachable organisations
    crude-xero tenant use <name|id>                 # pin a default tenant
    crude-xero organisation get
    crude-xero <resource> list [--where "<filter>"] [--order "<field>"] [--json]
    crude-xero <resource> get <guid>
    crude-xero <resource> create (--data '<json>' | -f file | stdin) [--yes]
    crude-xero <resource> update <guid> (--data '<json>' | -f file | stdin) [--yes]
    crude-xero <resource> delete <guid> [--yes]

Accounting resources: account, bank-transaction, bank-transfer, batch-payment, branding-theme (ro), budget (ro), contact, contact-group, credit-note, currency, employee, invoice, item, journal (ro), linked-transaction, manual-journal, organisation (ro), overpayment, payment, payment-service, prepayment, purchase-order, quote, receipt, repeating-invoice, report (ro), tax-rate, tracking-category, user. Not every resource takes every verb: read-only ones are list/get only; bank-transfer/currency/payment-service have no update; `payment`/`batch-payment` delete and `contact` archive post a status change rather than hard-deleting. Irregular verbs: `invoice email <guid>` (sends real mail), the binary `invoice online-url` and the `*.pdf` getters (`invoice`, `quote`, `purchase-order`, `credit-note`) write to `--out <path>`; `credit-note`/`overpayment`/`prepayment allocate <guid> --data ...`; `contact-group member add/remove`; `tracking-category option add/update/delete`; `tax-rate update` posts the whole object (no guid).

    crude-xero report list
    crude-xero report balance-sheet|profit-and-loss|trial-balance|aged-receivables|aged-payables|bank-summary|bas|gst|executive-summary|budget-summary [--date] [--from-date] [--to-date] [--param KEY=VALUE]
    crude-xero attachment list|get|add --on <resource> --id <guid> [--file ...] [--out ...] [--mime ...]
    crude-xero history list|add --on <resource> --id <guid> [--note ...]

`update` is read-merge-write: crude fetches the object, overlays your `--data`/flags, and posts the whole back, so an update changes only what you pass. The other Xero APIs (Payroll, Files, Assets, Projects, BankFeeds, Finance) are planned but not in this binary yet; see the crude repo docs/xero.md.

## crude-airwallex (airwallex.com)

Airwallex global payments and transactions over the official REST API. Credentials in `[airwallex]` (`client_id`, `api_key`; optional `environment = "demo"`, optional `on_behalf_of` for platform accounts); the bearer token is fetched on first use and cached in `~/.local/state/crude/airwallex_token.json`, refreshed on expiry. There is no consent step; `crude-airwallex login` just confirms the credentials. All timestamps are shown in the `timezone` the config names (under `[airwallex]`, else at the top level; the machine's zone when neither is set), and `--from`/`--to` are YYYY-MM-DD dates read in that zone and converted to UTC.

    crude-airwallex login                                   # confirm credentials, report token expiry
    crude-airwallex account get
    crude-airwallex balance current
    crude-airwallex balance history [--currency] [--from] [--to] [--limit]
    crude-airwallex transaction list [--currency] [--status] [--from] [--to] [--all] [--limit]
    crude-airwallex transaction get <id>
    crude-airwallex beneficiary list|get|create|update|delete
    crude-airwallex transfer list|get ; transfer create (--data | -f | stdin) [--yes]      # moves real money
    crude-airwallex fx-rate current --buy <ccy> --sell <ccy> [--amount]
    crude-airwallex conversion list|get ; conversion create (--data | -f | stdin) [--yes]  # moves real money
    crude-airwallex pa payment-intent list|get|create|confirm|capture|cancel
    crude-airwallex pa refund list|get|create ; pa customer list|get|create|update|delete
    crude-airwallex pa payment-consent list|get ; pa payment-link list|get|create

Add `--json` to any read for the raw object. Money-moving verbs (transfer/conversion create, pa payment-intent create/confirm/capture, pa refund create, pa payment-link create) prompt unless `--yes`. The `pa` group needs Payments Acceptance enabled on the account; a product that is not enabled reports "API access for this resource has been disabled". Field-name casing is not uniform (financial_transactions is camelCase, the others snake_case); the verified surface and specifics are in the crude repo docs/airwallex.md.

## crude-clover (clover.com)

AP Clover POS over the documented REST API. A static Bearer token in `[clover]` (`api_token`), issued once from the AP production dashboard (Setup -> API Tokens, Read on Merchant/Inventory/Orders/Payments); there is no login step, and the merchant id is resolved at runtime, never stored. Orders and the catalog go to a file, not stdout: a year of orders is tens of MB.

    crude-clover orders list --from YYYY-MM-DD --to YYYY-MM-DD [--tz IANA] -o PATH.jsonl
    crude-clover catalog dump -o PATH.json
    crude-clover flatten PATH.jsonl --catalog PATH.json -o PATH.csv [--tz IANA]

`orders list` writes JSONL (one Order per line) with line items, modifications, payments, and refunds expanded; it slices the range by local day and splits further if a window exceeds Clover's 10000-offset cap. The category dimension is not on line items, so `catalog dump` exposes each item's raw Clover category for `flatten` to join. `flatten` renders the orders into the legacy Square item-level CSV column shape (one row per line item, plus one negative-Net Sales row per refund), so an analysis built on Square exports can read Clover data; `Category` carries the raw Clover category, and mapping it into report buckets is the analysis's job. `--tz` defaults to Australia/Brisbane and sets both the date bounds and the local Date/Time columns.

## crude-facebook (graph.facebook.com)

Facebook Page posts, insights, and comments over the Graph API. A bearer token in `[facebook]` (`access_token`, `page_id`; optional `app_secret`); there is no login step. The Page id is resolved at runtime from `/me/accounts`, with `page_id` as a fallback when that edge is empty (a Page managed in Business Manager). Page writes need a Page access token; for a Business-managed Page, use a System User token with the Page assigned and set `page_id`. See the crude repo docs/facebook.md for acquiring a durable token.

    crude-facebook status                        # token check + resolved page id/name
    crude-facebook post list [--scheduled] [--limit N] ; post get <id> ; post insights <id> [--metric ...]
    crude-facebook post create [-m <s>] [--link <u>] [--photo-url <u>] [--schedule <time>] [--yes]
    crude-facebook post edit <id> -m <s> [--yes] ; post delete <id> [--yes]
    crude-facebook comment list <post-id> ; comment reply <object-id> -m <s> ; comment hide|unhide|delete <comment-id>
    crude-facebook page get ; page insights [--metric ...] [--period day]

Add `--json` to any read for the raw Graph object. Writes prompt unless `--yes`. Insight metric names shift between Graph versions (`impressions` is gone in favour of `views`, `page_fans` in favour of `page_follows`), so the insight commands take `--metric` to override the defaults. Constraints worth knowing: a Facebook post edit changes only the message and only on posts this app created, and the Page events edge is not reachable (Meta restricts it to Marketing Partners). On the venue's own Page the full surface runs without Meta App Review. Instagram is a separate product on Meta's roadmap and is not in this binary.

## crude-mautic (self-hosted Mautic)

Marketing automation: website forms and their submissions, contacts, segments, campaigns, and email send counts. `[mautic]` holds `base_url` (the instance, no `/api` suffix), `username` and `password`, sent as HTTP basic auth; there is no login step. Both the API and HTTP basic auth are switches under Configuration > API Settings in Mautic, off by default.

    crude-mautic status                          # credential check + signed-in user
    crude-mautic form list ; form get <id|alias> # `get` also lists the form's fields
    crude-mautic form submissions <id|alias> [--where FIELD=VALUE] [--group-by FIELD] [--field F] [--limit N]
    crude-mautic contact list [--search <q>] [--limit N] ; contact get <id>
    crude-mautic segment list ; segment get <id> ; campaign list ; campaign get <id>
    crude-mautic email list ; email get <id>     # list carries sent and read counts

A form is addressed by numeric id or by the alias its page markup carries. One Mautic form commonly serves several website pages that mark their traffic with a hidden field, so counting a single page's submissions means filtering a shared form: `--where topic="Stallholder EOI"` narrows to that page's slice, and `--group-by topic` counts every slice at once. Answers are HTML-unescaped before matching, so a value stored two ways counts once. `--search` on contacts takes Mautic's own syntax (an email, or `segment:alias`). This client reads; it does not create or edit Mautic records.

## crude-wise (wise.com)

Wise Business over the documented REST API. A personal API token in `[wise]` (`api_token`, issued under Your Account > Connect and manage apps > API tokens and pasted as issued, UUID or JWT); no login step. Reads act on the account's business profile (`profile_id` pins one). `transaction list` is the balance statement, SCA-protected for profiles registered outside US/AU/NZ/SG/CA/MY: it needs `private_key` (an RSA key whose public half is uploaded on the API tokens page) and without one reports the setup. The client reads only.

    crude-wise status                                   # token check; the profile in use and currencies held
    crude-wise profile list ; profile get [<id>]
    crude-wise balance list
    crude-wise transaction list (--currency <ccy> | --balance <id>) --from YYYY-MM-DD [--to] [--type COMPACT|FLAT] [--limit]
    crude-wise transfer list [--status] [--from] [--to] [--limit] ; transfer get <id>
    crude-wise recipient list [--currency] [--limit] ; recipient get <id>
    crude-wise activity list [--from] [--to] [--status] [--limit]

A second Wise account sits as `[wise.<name>]`, selected with `--account <name>`. Statement windows span at most 469 days. Activity amounts are display strings (`1,220 AUD`); the statement's are numbers.
"""


def claude_dir() -> Path:
    """Return the Claude Code configuration directory this session reads.

    ``CLAUDE_CONFIG_DIR`` names it when set, ``~/.claude`` otherwise. One
    machine commonly carries several such directories, and a command written
    into the wrong one is invisible to the session that needed it.
    """
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def command_file() -> Path:
    return claude_dir() / "commands" / f"{COMMAND_NAME}.md"


def skill_dir() -> Path:
    """A skill of the same name, if the user keeps one, supersedes the command."""
    return claude_dir() / "skills" / COMMAND_NAME


def _superseded() -> bool:
    """True when Claude Code is absent, or a same-named skill supersedes the command."""
    return not claude_dir().exists() or skill_dir().exists()


def refresh() -> None:
    """Rewrite the command file when it is missing or differs from COMMAND.

    Idempotent and silent. Does nothing when Claude Code is not installed or a
    same-named skill supersedes the command. "Out of date" is content inequality
    with COMMAND, so no version field is needed.
    """
    if _superseded():
        return
    f = command_file()
    if f.exists() and f.read_text() == COMMAND:
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(COMMAND)


def version_callback(value: bool) -> None:
    """Eager ``--version`` handler shared by every crude CLI: print and exit.

    Wired into each app's root callback so ``crude`` and the site binaries all
    report the same number, sourced once from package metadata.
    """
    if value:
        typer.echo(crude_version())
        raise typer.Exit()


def add_install_command(app) -> None:
    """Attach the install-claude-command subcommand to ``app``.

    Separate from the root callback so the crude umbrella, which needs its own
    callback to list the site commands, can still register the subcommand.
    """

    @app.command("install-claude-command")
    def install_claude_command():
        """(Re)write the crude command for Claude Code."""
        if _superseded():
            typer.echo(
                "Skipped: Claude Code is not installed, or a same-named skill "
                "supersedes the command."
            )
            return
        f = command_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(COMMAND)
        typer.echo(f"Installed: {f}")


def register_claude_command(app) -> None:
    """Attach the shared root callback and the install-claude-command subcommand.

    The root callback keeps the installed command file equal to COMMAND on
    every invocation and handles the shared ``--version`` flag; the subcommand does
    the same write explicitly, with feedback. Used by the site CLIs; the crude
    umbrella wires its own callback and calls ``add_install_command`` directly.
    """

    @app.callback()
    def _root(
        version: bool = typer.Option(
            None, "--version", callback=version_callback, is_eager=True, help=VERSION_HELP
        ),
        account: Optional[str] = typer.Option(
            None, "--account", "-a", envvar="CRUDE_ACCOUNT", help=ACCOUNT_HELP
        ),
    ):
        # The WORLD_AS_OF gate runs before anything else: a set-but-unparseable
        # bound must abort the process before any request could fire.
        asof.check_env()
        set_account(account)
        refresh()

    add_install_command(app)
