"""Install and keep current the crude command for Claude Code.

This installs a Claude Code *command* (``~/.claude/commands/crude.md``), not a
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

from pathlib import Path
from typing import Optional

import typer

from crude_common import version as crude_version
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
description: Read and edit your own data on atdw-online.com.au (ATDW tourism listings), australia.skal.org (Skal Australia member portal), rezdy.com (products, availability, bookings), deputy.com (rostering, timesheets, leave, employees), and app.sonas.events (Sonas wedding-venue events).
allowed-tools: Bash
---

# crude

crude provides command-line clients for reading and editing your own data on sites that lack a usable public API. Each site is its own binary. Configuration for all of them lives in `~/.config/crude/config.toml` (sections `[atdw]`, `[skal]`, `[rezdy]`, `[deputy]`, `[sonas]`). Add `--json` to any read command for machine-readable output.

A site can hold several accounts. The bare `[site]` section is the default account; a `[site.<name>]` subtable is a named one. Select it with `--account/-a <name>` before the resource (or `$CRUDE_ACCOUNT`), e.g. `crude-rezdy --account es booking cancellations --from 2026-05-03`. Without `--account`, the default account is used.

## crude-atdw (atdw-online.com.au)

Tourism listings. Credentials in `[atdw]`; the JWT token is cached and renewed automatically.

    crude-atdw login
    crude-atdw listing list [--scope own|all] [--type] [--city] [--state] [--status] [--name] [--limit] [--offset]
    crude-atdw listing get <id>
    crude-atdw listing update <id> <field> <value>
    crude-atdw listing submit <id>

`listing list` with no filters returns your own organisation's listings; any filter flag or `--scope all` searches every visible listing.

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

Rezdy Supplier API. API key in `[rezdy]` (`api_key`, required `timezone` as an IANA name, optional `environment`); there is no login step. rezdy reads every typed date as the account's operational day, so any command errors if `timezone` is missing.

    crude-rezdy product list [--search] [--limit] [--offset]
    crude-rezdy product get <code>
    crude-rezdy availability list --product <code> --from "<YYYY-MM-DD HH:mm:ss>" --to "<...>" [--min-availability] [--limit]
    crude-rezdy booking list [--status] [--search] [--product] [--from] [--to] [--created-from] [--created-to] [--updated-from] [--updated-to] [--limit] [--offset] [--all]
    crude-rezdy booking cancellations [--from <YYYY-MM-DD>] [--to <YYYY-MM-DD>] [--limit] [--all]
    crude-rezdy booking get <orderno>

`booking cancellations` filters by when the cancellation occurred (dateUpdated), not the session date. Use --from/--to with YYYY-MM-DD dates.
--updated-from / --updated-to on `booking list` apply the same client-side filter to any status.
These two filters compare against a UTC instant; crude reads the typed date as the account's operational day (the required `timezone`) and converts to UTC, so a boundary date is not off by one.
--all on either command fetches all pages automatically (default limit is otherwise applied).
For one day's bookings, set --from and --to to that day's bounds. Availability times are local (`YYYY-MM-DD HH:mm:ss`); booking times are ISO 8601.

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
    crude-sonas report list [--json]
    crude-sonas report get <reportId> [--json]

Event status values: Enquiry, Confirmed, Cancelled, DateOnHold, Exhausted, ConfirmedPending, Completed, Idle. A fresh enquiry has no event date and stays out of `event list` until hold-date or change-date sets one; hold-date also sets DateOnHold, change-date keeps the status. change-status prompts when the target leaves the enquiry group (Enquiry, DateOnHold, Exhausted, Idle); delete and cancel prompt unless --yes. The full resource map (events, finance, guests, timelines, service-bookings, and more) and the remaining subcommand plan live in the crude repo docs/sonas.md.

Named guests (guest list/add/update/delete) and the headcount (guest set-numbers, the currentMain counts shown by event list) are separate records: adding an attending guest auto-increments the matching count, deleting a guest does not decrement it, and set-numbers refuses to go below the named guestlist's total for a type.

Timeline entries are absolute (--time, naive ISO counts as UTC) or relative to another entry (--after + --offset-minutes, negative = before); timeline update takes a full replacement entry, not a modifier; timeline import appends a tenant template's entries (template ids are the eventId-less docs in the timelines collection). Note and timeline --section take an EventSectionEnum slug (notes, general, timeline, bar, ...; the table is in the crude repo docs/sonas.md); note add defaults to notes.

service-booking cancel keeps the booking as a Cancelled record (Sonas has no booking delete); edit replaces the whole option list. Option ids come from the service's catalog doc.

The finance, mail, and terms writes ship uncalled (they touch finance/Xero, send real mail, or alter contract state); each says so in --help. The charge/refund/discount --data doc: amount (>= 0) and dueDate (EJSON, {"$date": <epoch-ms>}) required, description optional; refund also needs method (payment-method name maps to a number: Cash 0, Card 1, Cheque 2, Transfer 3, DirectDebit 4, EscrowAccount 5, OnlineBankTransfer 6, Other 100) and financialRecordId; charge also accepts categoryId and sectionId. payment takes flat typed flags instead of --data; terms accept accepts every pending terms record on the event.

Appointment --type takes a name or number: ShowAround, Meeting, Holiday, OpenDay, ItemDelivery, Tasting, Maintenance, PhotoShoot, Accommodation, Ceremony, InternalMeeting, CustomAppointment1-3, RegularEvent. An InternalMeeting with no --event link is a plain staff-calendar entry; the customer appointment types send reminder mail. Commands marked unverified have their payloads decoded but were never trial-called; see docs/sonas.md §6 before relying on them.

`<catalog>` is one of the read-only catalog resources: supplier, service, drinks-package, package, template, category, venue, user. --search matches a case-insensitive substring anywhere in the document.
"""


def command_file() -> Path:
    return Path.home() / ".claude" / "commands" / f"{COMMAND_NAME}.md"


def skill_dir() -> Path:
    """A skill of the same name, if the user keeps one, supersedes the command."""
    return Path.home() / ".claude" / "skills" / COMMAND_NAME


def _superseded() -> bool:
    """True when Claude Code is absent, or a same-named skill supersedes the command."""
    return not (Path.home() / ".claude").exists() or skill_dir().exists()


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

    The root callback keeps ``~/.claude/commands/crude.md`` equal to COMMAND on
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
        set_account(account)
        refresh()

    add_install_command(app)
