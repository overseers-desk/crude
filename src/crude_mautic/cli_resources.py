"""Mautic resources for crude-mautic: forms, contacts, segments, campaigns, emails.

Explicit command groups mounted on the root (the grammar is ``crude-mautic
<resource> <verb>``). The read surface is what a marketing operator asks of the
instance from a terminal: which forms exist, who submitted one, how many, and what
the contact, segment, campaign and email records behind them say.

``form submissions`` carries the counting surface, because a Mautic form is
routinely shared across several pages that mark their traffic with a hidden field:
one form receives every website enquiry while a hidden ``topic`` says which page it
came from. ``--where`` narrows to one such slice and ``--group-by`` counts every
slice at once, so a shared form answers per-page questions without a form per page.

Two behaviours of the live API shape this layer. Mautic returns HTTP 500 for an
``orderBy`` of ``dateSubmitted`` on the submissions endpoint, so ordering is done
here after fetching rather than asked of the server. And a form is addressed by its
numeric id or by its alias, since the alias is what the page markup carries.
"""

from __future__ import annotations

from collections import Counter
from typing import List, Optional

import typer

from crude_common import asof
from crude_common.config import s, site_timezone
from crude_common.localtime import format_local
from crude_common.output import emit_list, emit_record
from crude_mautic.client import MauticError, rows, unescape_results

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")
_LIMIT = typer.Option(25, "--limit", help="Maximum records to fetch.")


def _when(field: str):
    """A column showing a Mautic timestamp in the timezone the config names,
    or in the machine's when it names none."""
    return lambda rec: format_local(rec.get(field), tz=site_timezone("mautic"))


_FORM_COLS = [
    ("ID", "id"), ("Alias", "alias"), ("Name", "name"),
    ("Published", "isPublished"), ("Added", _when("dateAdded")),
]
_SEGMENT_COLS = [
    ("ID", "id"), ("Alias", "alias"), ("Name", "name"), ("Public", "isGlobal"),
]
_CAMPAIGN_COLS = [
    ("ID", "id"), ("Name", "name"), ("Published", "isPublished"),
    ("Added", _when("dateAdded")),
]
_EMAIL_COLS = [
    ("ID", "id"), ("Name", "name"), ("Subject", "subject"),
    ("Sent", "sentCount"), ("Read", "readCount"), ("Published", "isPublished"),
]
_COUNT_COLS = [("Value", "value"), ("Count", "count")]


def _session():
    from crude_mautic.cli import _session as impl

    return impl()


def _fail(what: str, e: Exception):
    typer.echo(f"Error fetching {what}: {e}", err=True)
    raise typer.Exit(1)


form = typer.Typer(help="Mautic forms and their submissions.")
contact = typer.Typer(help="Mautic contacts.")
segment = typer.Typer(help="Mautic segments (contact lists).")
campaign = typer.Typer(help="Mautic campaigns.")
email = typer.Typer(help="Mautic emails and their send counts.")


# --------------------------------------------------------------------------
# form
# --------------------------------------------------------------------------

@form.command("list", help="List the instance's forms.")
def form_list(limit: int = _LIMIT, output_json: bool = _JSON):
    sess = _session()
    try:
        items = sess.fetch("/forms", "forms", limit=limit, created="dateAdded",
                           modified="dateModified", what="form")
    except MauticError as e:
        _fail("forms", e)
    emit_list(items, _FORM_COLS, "form", output_json)


@form.command("get", help="Show one form by id or alias, with its fields.")
def form_get(
    form_ref: str = typer.Argument(..., metavar="ID|ALIAS", help="Form id or alias."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        rec = _resolve_form(sess, form_ref)
    except MauticError as e:
        _fail(f"form {form_ref}", e)
    rec = asof.check_record(rec, "dateAdded", "dateModified", what="form")
    if output_json:
        emit_record(rec, True)
        return
    fields = rec.get("fields") or {}
    summary = {
        "id": rec.get("id"), "alias": rec.get("alias"), "name": rec.get("name"),
        "description": rec.get("description"), "published": rec.get("isPublished"),
        "added": _when("dateAdded")(rec), "fields": len(fields),
    }
    emit_record(summary, False)
    field_rows = list(fields.values()) if isinstance(fields, dict) else list(fields)
    if field_rows:
        emit_list(field_rows,
                  [("Alias", "alias"), ("Label", "label"), ("Type", "type"),
                   ("Required", lambda f: (f.get("isRequired")))],
                  "field", False)


@form.command("submissions", help="List a form's submissions; filter and count them.")
def form_submissions(
    form_ref: str = typer.Argument(..., metavar="ID|ALIAS", help="Form id or alias."),
    where: Optional[List[str]] = typer.Option(
        None, "--where", "-w", metavar="FIELD=VALUE",
        help="Keep only submissions whose answer matches; repeatable (all must match)."),
    group_by: Optional[str] = typer.Option(
        None, "--group-by", "-g", metavar="FIELD",
        help="Count submissions per distinct answer to FIELD instead of listing them."),
    field: Optional[List[str]] = typer.Option(
        None, "--field", "-f", metavar="FIELD",
        help="Show only these answer columns; repeatable. Default: every answer."),
    limit: int = typer.Option(
        0, "--limit", help="Maximum submissions to show; 0 (the default) shows all."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        form_rec = _resolve_form(sess, form_ref)
        items = sess.fetch(f"/forms/{form_rec['id']}/submissions", "submissions",
                           created="dateSubmitted", what="submission")
    except MauticError as e:
        _fail(f"submissions for form {form_ref}", e)
    items = [unescape_results(i) for i in items]
    items.sort(key=lambda i: s(i.get("dateSubmitted")), reverse=True)

    for clause in where or []:
        if "=" not in clause:
            raise typer.BadParameter(
                f"--where takes FIELD=VALUE, got {clause!r}", param_hint="--where")
        key, _, value = clause.partition("=")
        items = [i for i in items
                 if s((i.get("results") or {}).get(key.strip())) == value.strip()]

    if group_by:
        counts = Counter(
            s((i.get("results") or {}).get(group_by)) or "(unanswered)" for i in items)
        tally = [{"value": v, "count": n} for v, n in counts.most_common()]
        emit_list(tally, _COUNT_COLS, f"distinct {group_by}", output_json)
        if not output_json:
            typer.echo(f"{len(items)} submission(s) across them.")
        return

    if limit:
        items = items[:limit]
    # --json ignores the columns, so they are only built for the table.
    columns = []
    if not output_json:
        columns = [("ID", "id"), ("Submitted", _when("dateSubmitted"))]
        columns += [(a, (lambda k: lambda i: (i.get("results") or {}).get(k))(a))
                    for a in field or _answer_fields(items)]
    emit_list(items, columns, "submission", output_json)


def _answer_fields(items: list) -> list:
    """The answer keys present across submissions, in first-seen order."""
    seen = []
    for item in items:
        for key in (item.get("results") or {}):
            if key not in seen:
                seen.append(key)
    return seen


def _resolve_form(sess, form_ref: str) -> dict:
    """The form record for a numeric id, or for an alias matched against the list."""
    if form_ref.isdigit():
        return sess.one(f"/forms/{form_ref}", "form")
    listed = rows(sess.get("/forms", params={"limit": 200}), "forms")
    for item in listed:
        if item.get("alias") == form_ref:
            return item
    aliases = ", ".join(sorted(s(i.get("alias")) for i in listed))
    typer.echo(
        f"Error: no form with alias {form_ref!r}. Known aliases: {aliases}", err=True)
    raise typer.Exit(1)


# --------------------------------------------------------------------------
# contact
# --------------------------------------------------------------------------

def _contact_field(rec: dict, name: str):
    """One contact field, read from the ``fields.all`` map the API returns."""
    fields = rec.get("fields") or {}
    allf = fields.get("all") if isinstance(fields, dict) else None
    return (allf or {}).get(name)


_CONTACT_COLS = [
    ("ID", "id"),
    ("Email", lambda c: _contact_field(c, "email")),
    ("First", lambda c: _contact_field(c, "firstname")),
    ("Last", lambda c: _contact_field(c, "lastname")),
    ("Points", "points"),
    ("Added", _when("dateAdded")),
]


@contact.command("list", help="List contacts, newest first; --search takes Mautic's syntax.")
def contact_list(
    search: Optional[str] = typer.Option(
        None, "--search", help="Mautic search string, e.g. an email or `segment:alias`."),
    limit: int = _LIMIT,
    output_json: bool = _JSON,
):
    sess = _session()
    params = {"search": search} if search else {}
    params["orderBy"] = "date_added"
    params["orderByDir"] = "DESC"
    try:
        items = sess.fetch("/contacts", "contacts", params=params, limit=limit,
                           created="dateAdded", modified="dateModified",
                           what="contact")
    except MauticError as e:
        _fail("contacts", e)
    emit_list(items, _CONTACT_COLS, "contact", output_json)


@contact.command("get", help="Show one contact by id.")
def contact_get(
    contact_id: str = typer.Argument(..., help="Contact id."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        rec = sess.one(f"/contacts/{contact_id}", "contact")
    except MauticError as e:
        _fail(f"contact {contact_id}", e)
    rec = asof.check_record(rec, "dateAdded", "dateModified", what="contact")
    if output_json:
        emit_record(rec, True)
        return
    allf = ((rec.get("fields") or {}).get("all")) or {}
    summary = {"id": rec.get("id"), "points": rec.get("points"),
               "added": _when("dateAdded")(rec), "last_active": _when("lastActive")(rec)}
    # fields.all repeats last_active as a contact field, in the form Mautic stores
    # it; the summary's own line, in the timezone shown everywhere else, stands.
    summary.update({k: v for k, v in allf.items()
                    if v not in (None, "") and k not in summary})
    emit_record(summary, False)


# --------------------------------------------------------------------------
# segment, campaign, email
# --------------------------------------------------------------------------

@segment.command("list", help="List segments (contact lists).")
def segment_list(limit: int = _LIMIT, output_json: bool = _JSON):
    sess = _session()
    try:
        items = sess.fetch("/segments", "segments", limit=limit, created="dateAdded",
                           modified="dateModified", what="segment")
    except MauticError as e:
        _fail("segments", e)
    emit_list(items, _SEGMENT_COLS, "segment", output_json)


@segment.command("get", help="Show one segment by id.")
def segment_get(
    segment_id: str = typer.Argument(..., help="Segment id."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        rec = sess.one(f"/segments/{segment_id}", "list")
    except MauticError as e:
        _fail(f"segment {segment_id}", e)
    emit_record(asof.check_record(rec, "dateAdded", "dateModified", what="segment"), output_json)


@campaign.command("list", help="List campaigns.")
def campaign_list(limit: int = _LIMIT, output_json: bool = _JSON):
    sess = _session()
    try:
        items = sess.fetch("/campaigns", "campaigns", limit=limit, created="dateAdded",
                           modified="dateModified", what="campaign")
    except MauticError as e:
        _fail("campaigns", e)
    emit_list(items, _CAMPAIGN_COLS, "campaign", output_json)


@campaign.command("get", help="Show one campaign by id.")
def campaign_get(
    campaign_id: str = typer.Argument(..., help="Campaign id."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        rec = sess.one(f"/campaigns/{campaign_id}", "campaign")
    except MauticError as e:
        _fail(f"campaign {campaign_id}", e)
    emit_record(asof.check_record(rec, "dateAdded", "dateModified", what="campaign"), output_json)


@email.command("list", help="List emails with their sent and read counts.")
def email_list(limit: int = _LIMIT, output_json: bool = _JSON):
    sess = _session()
    try:
        items = sess.fetch("/emails", "emails", limit=limit, created="dateAdded",
                           modified="dateModified", what="email")
    except MauticError as e:
        _fail("emails", e)
    # The send and read counts are running totals with no as-of form: an email
    # that existed before the cutoff still reports what it has sent by now.
    items = asof.current_state(items, "email send and read counts (running totals)")
    emit_list(items, _EMAIL_COLS, "email", output_json)


@email.command("get", help="Show one email by id.")
def email_get(
    email_id: str = typer.Argument(..., help="Email id."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        rec = sess.one(f"/emails/{email_id}", "email")
    except MauticError as e:
        _fail(f"email {email_id}", e)
    rec = asof.check_record(rec, "dateAdded", "dateModified", what="email")
    rec = asof.current_state(rec, "email send and read counts (running totals)")
    emit_record(rec, output_json)
