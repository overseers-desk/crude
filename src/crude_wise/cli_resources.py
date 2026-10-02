"""Wise resources for crude-wise: profiles, balances, the statement, transfers,
recipients, activities.

The read surface is what a business owner asks of the account from a terminal:
what is held, what moved and when, who was paid, and the ledger behind a balance
for the bookkeeper.

``transaction list`` is the balance statement: Wise's only numeric ledger, keyed
by balance and bounded to a window of at most 469 days. ``activity list`` is the
feed the Wise app shows on its home screen, every kind of event in one stream,
with amounts as display strings rather than numbers.

Timestamps arrive as ISO-8601 UTC and render in the timezone the config names,
the machine's when it names none; typed ``--from``/``--to`` dates are read as days
in the same zone and sent as UTC instants.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

import typer

from crude_common import asof
from crude_common.config import site_timezone
from crude_common.localtime import format_local, to_utc_iso
from crude_common.output import emit_list, emit_record
from crude_wise.client import WiseError

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")
_LIMIT = typer.Option(25, "--limit", help="Maximum records to fetch.")
_FROM = typer.Option(None, "--from", help="From date YYYY-MM-DD (local).")
_TO = typer.Option(None, "--to", help="To date YYYY-MM-DD (local, inclusive).")

# Wise marks up activity titles and amounts with emphasis tags (<strong>,
# <positive>, ...); the table shows the text, --json keeps the markup.
_TAGS = re.compile(r"</?[a-z]+>")


def _plain(field: str):
    return lambda rec: _TAGS.sub("", rec.get(field) or "")


def _dig(rec: dict, path: str):
    """A dotted-path read of a nested field, None where any step is missing."""
    cur = rec
    for key in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _col(path: str):
    return lambda rec: _dig(rec, path)


def _zone():
    """The timezone the config names for the selected account, or None for the machine's."""
    return site_timezone("wise")


def _ts(path: str):
    return lambda rec: format_local(_dig(rec, path), tz=_zone())


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window(from_, to):
    """(start, end) UTC instants for a typed local window; end never in the
    future, and clamped to the WORLD_AS_OF bound when one is set."""
    start = to_utc_iso(from_, tz=_zone()) if from_ else None
    end = to_utc_iso(to, end=True, tz=_zone()) if to else _now_iso()
    end = min(end, _now_iso())
    asof.check_window_start(start)
    return start, asof.clamp_upper_iso(end)


_PROFILE_COLS = [
    ("ID", "id"), ("Type", "type"),
    ("Name", lambda p: p.get("businessName") or p.get("fullName")),
    ("Country", _col("address.countryIso2Code")), ("State", "currentState"),
    ("Created", _ts("createdAt")),
]
_BALANCE_COLS = [
    ("ID", "id"), ("Currency", "currency"), ("Type", "type"), ("Name", "name"),
    ("Available", _col("amount.value")), ("Reserved", _col("reservedAmount.value")),
    ("Total", _col("totalWorth.value")),
]
_TXN_COLS = [
    ("Date", _ts("date")), ("Type", _col("details.type")),
    ("Amount", _col("amount.value")), ("Currency", _col("amount.currency")),
    ("Fees", _col("totalFees.value")), ("Balance", _col("runningBalance.value")),
    ("Description", _col("details.description")), ("Ref", "referenceNumber"),
]
_TRANSFER_COLS = [
    ("ID", "id"), ("Status", "status"),
    ("Source", lambda t: f"{t.get('sourceValue')} {t.get('sourceCurrency')}"),
    ("Target", lambda t: f"{t.get('targetValue')} {t.get('targetCurrency')}"),
    ("Rate", "rate"), ("Reference", _col("details.reference")), ("Created", _ts("created")),
]
_RECIPIENT_COLS = [
    ("ID", "id"), ("Name", _col("name.fullName")), ("Currency", "currency"),
    ("Country", "country"), ("Type", "type"), ("Account", "accountSummary"),
    ("Active", "active"),
]
_ACTIVITY_COLS = [
    ("Created", _ts("createdOn")), ("Type", "type"), ("Status", "status"),
    ("Title", _plain("title")), ("Primary", _plain("primaryAmount")),
    ("Secondary", _plain("secondaryAmount")), ("Description", _plain("description")),
]


def _session():
    from crude_wise.cli import _session as impl

    return impl()


def _fail(what: str, e: Exception):
    typer.echo(f"Error fetching {what}: {e}", err=True)
    raise typer.Exit(1)


profile = typer.Typer(help="The token's Wise profiles (business and personal).")
balance = typer.Typer(help="Wise balances held by the profile.")
transaction = typer.Typer(help="The balance statement: the ledger behind one balance.")
transfer = typer.Typer(help="Wise transfers (payouts) made from the profile.")
recipient = typer.Typer(help="Wise recipients (payee accounts) of the profile.")
activity = typer.Typer(help="The profile's activity feed: every event, one stream.")


# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------

@profile.command("list", help="List the profiles the token reaches.")
def profile_list(output_json: bool = _JSON):
    sess = _session()
    try:
        items = sess.profiles()
    except WiseError as e:
        _fail("profiles", e)
    items = asof.current_state(items, "profiles")
    emit_list(items, _PROFILE_COLS, "profile", output_json)


@profile.command("get", help="Show one profile; the selected one when no id is given.")
def profile_get(
    profile_id: Optional[int] = typer.Argument(None, help="Profile id."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        rec = sess.profile(profile_id)
    except WiseError as e:
        _fail(f"profile {profile_id or ''}".strip(), e)
    rec = asof.check_record(rec, "createdAt", "updatedAt", what="profile")
    emit_record(rec, output_json)


# --------------------------------------------------------------------------
# balance
# --------------------------------------------------------------------------

@balance.command("list", help="Current balance per currency (and savings jar).")
def balance_list(output_json: bool = _JSON):
    if asof.active():
        # A balance is a now-value with no history; the bounded substitute is
        # `transaction list`, whose window is clamped to the cutoff.
        asof.refuse("a current balance is a now-value with no as-of history; "
                    "use `transaction list` instead")
    sess = _session()
    try:
        items = sess.balances()
    except WiseError as e:
        _fail("balances", e)
    emit_list(items, _BALANCE_COLS, "balance", output_json)


# --------------------------------------------------------------------------
# transaction (the balance statement)
# --------------------------------------------------------------------------

def _pick_balance(balances: list, currency: Optional[str], balance_id: Optional[int]) -> dict:
    """The balance a statement is read for: by id, else the currency's STANDARD
    balance (a savings jar of the same currency is reached by id)."""
    if balance_id is not None:
        for b in balances:
            if b.get("id") == balance_id:
                return b
        raise WiseError(f"no balance with id {balance_id} on this profile")
    cur = (currency or "").upper()
    same = [b for b in balances if (b.get("currency") or "").upper() == cur]
    standard = [b for b in same if b.get("type") == "STANDARD"]
    if standard:
        return standard[0]
    if same:
        return same[0]
    held = ", ".join(sorted({b.get("currency") for b in balances if b.get("currency")})) or "none"
    raise WiseError(f"no {cur} balance on this profile (held: {held})")


@transaction.command("list", help="The statement rows of one balance over a window (at most 469 days).")
def transaction_list(
    currency: Optional[str] = typer.Option(None, "--currency", help="Balance currency, e.g. AUD."),
    balance_id: Optional[int] = typer.Option(None, "--balance", help="Balance id (a savings jar, or instead of --currency)."),
    from_: Optional[str] = _FROM,
    to: Optional[str] = _TO,
    kind: str = typer.Option("COMPACT", "--type", help="COMPACT (one line per transaction) or FLAT (fees on their own line)."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum rows to show."),
    output_json: bool = _JSON,
):
    if not (currency or balance_id):
        raise typer.BadParameter("give --currency or --balance")
    if not from_:
        raise typer.BadParameter("--from is required (a statement always has a window)")
    start, end = _window(from_, to)
    sess = _session()
    try:
        bal = _pick_balance(sess.balances(), currency, balance_id)
        stmt = sess.statement(bal["id"], bal["currency"], start=start, end=end,
                              kind=kind.upper())
    except WiseError as e:
        _fail("statement", e)
    rows = stmt.get("transactions") or [] if isinstance(stmt, dict) else []
    rows = asof.bound_records(rows, "date", what="transaction")
    if limit:
        rows = rows[:limit]
    emit_list(rows, _TXN_COLS, "transaction", output_json)


# --------------------------------------------------------------------------
# transfer
# --------------------------------------------------------------------------

@transfer.command("list", help="List transfers made from the profile.")
def transfer_list(
    status: Optional[str] = typer.Option(None, "--status", help="Comma-separated Wise statuses, e.g. outgoing_payment_sent."),
    from_: Optional[str] = _FROM,
    to: Optional[str] = _TO,
    limit: int = _LIMIT,
    output_json: bool = _JSON,
):
    sess = _session()
    start = to_utc_iso(from_, tz=_zone()) if from_ else None
    asof.check_window_start(start)
    end = asof.clamp_upper_iso(to_utc_iso(to, end=True, tz=_zone()) if to else None)
    params = {"status": status, "createdDateStart": start, "createdDateEnd": end}
    params = {k: v for k, v in params.items() if v is not None}
    try:
        items = sess.collect(sess.iter_transfers, params=params, limit=limit,
                             created="created", what="transfer")
    except WiseError as e:
        _fail("transfers", e)
    emit_list(items, _TRANSFER_COLS, "transfer", output_json)


@transfer.command("get", help="Show one transfer by id.")
def transfer_get(transfer_id: int = typer.Argument(..., help="Transfer id."),
                 output_json: bool = _JSON):
    sess = _session()
    try:
        rec = sess.get(f"/transfers/{transfer_id}")
    except WiseError as e:
        _fail(f"transfer {transfer_id}", e)
    rec = asof.check_record(rec, "created", what="transfer")
    emit_record(rec, output_json)


# --------------------------------------------------------------------------
# recipient
# --------------------------------------------------------------------------

@recipient.command("list", help="List the profile's recipients (payee accounts).")
def recipient_list(
    currency: Optional[str] = typer.Option(None, "--currency", help="Filter by target currency; comma-separated for several."),
    limit: int = _LIMIT,
    output_json: bool = _JSON,
):
    sess = _session()
    params = {"currency": currency} if currency else None
    try:
        items = list(sess.iter_recipients(params=params, max_items=limit))
    except WiseError as e:
        _fail("recipients", e)
    # A recipient record carries no creation or edit stamp, so under a bound it
    # is served as current state, disclosed.
    items = asof.current_state(items, "recipients")
    emit_list(items, _RECIPIENT_COLS, "recipient", output_json)


@recipient.command("get", help="Show one recipient by id.")
def recipient_get(recipient_id: int = typer.Argument(..., help="Recipient id."),
                  output_json: bool = _JSON):
    sess = _session()
    try:
        rec = sess.get(f"/accounts/{recipient_id}")
    except WiseError as e:
        _fail(f"recipient {recipient_id}", e)
    rec = asof.current_state(rec, "the recipient record")
    emit_record(rec, output_json)


# --------------------------------------------------------------------------
# activity
# --------------------------------------------------------------------------

@activity.command("list", help="The profile's activity feed, newest first.")
def activity_list(
    status: Optional[str] = typer.Option(None, "--status", help="REQUIRES_ATTENTION, IN_PROGRESS, UPCOMING, COMPLETED or CANCELLED."),
    from_: Optional[str] = _FROM,
    to: Optional[str] = _TO,
    limit: int = _LIMIT,
    output_json: bool = _JSON,
):
    sess = _session()
    start = to_utc_iso(from_, tz=_zone()) if from_ else None
    asof.check_window_start(start)
    end = asof.clamp_upper_iso(to_utc_iso(to, end=True, tz=_zone()) if to else None)
    params = {"status": status, "since": start, "until": end}
    params = {k: v for k, v in params.items() if v is not None}
    try:
        items = sess.collect(sess.iter_activities, params=params, limit=limit,
                             created="createdOn", modified="updatedOn", what="activity")
    except WiseError as e:
        _fail("activities", e)
    emit_list(items, _ACTIVITY_COLS, "activity", output_json)
