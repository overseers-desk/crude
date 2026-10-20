"""Business Profile resources for crude-business-profile: locations and reviews.

The read surface is what a business owner asks from a terminal: which locations
the account manages, what customers wrote and when, and which reviews still
await the business's reply. The one write is that reply, and its removal.

A location is named with ``--location/-l`` (a title substring, a store code, or
the resource name) or by ``default_location`` in config; an account managing a
single location needs neither. Timestamps arrive as ISO-8601 UTC and render in
the timezone the config names, the machine's when it names none; a typed
``--since`` date is read as a day in the same zone.
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_business_profile.client import (
    BusinessProfileError,
    address_line,
    place_id,
    replied,
    stars,
)
from crude_common import asof
from crude_common.config import site_timezone
from crude_common.localtime import format_local, parse_iso_utc, to_utc_iso
from crude_common.output import emit_list, emit_record
from crude_common.writeio import do_write

SITE = "business-profile"

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")
_YES = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt.")
_LOCATION = typer.Option(
    None, "--location", "-l",
    help="The location: a title substring, a store code, or accounts/<id>/locations/<id>; "
         "else config default_location; else the one location managed.")


def _session():
    from crude_business_profile.cli import _session as impl

    return impl()


def _resolve(sess, spec) -> str:
    from crude_business_profile.cli import default_location

    try:
        return sess.resolve_location(spec, default_location())
    except BusinessProfileError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


def _zone():
    return site_timezone(SITE)


def _when(field: str):
    return lambda rec: format_local(rec.get(field), tz=_zone())


def _reviewer(rec: dict) -> str:
    return (rec.get("reviewer") or {}).get("displayName") or "Anonymous"


def _reply_text(rec: dict) -> str:
    return (rec.get("reviewReply") or {}).get("comment") or ""


_LOCATION_COLS = [
    ("Title", "title"), ("Store code", "storeCode"), ("Address", address_line),
    ("Place ID", place_id), ("Resource", "resource"),
]
_REVIEW_COLS = [
    ("Written", _when("createTime")), ("Stars", stars), ("Reviewer", _reviewer),
    ("Replied", lambda r: "yes" if replied(r) else "no"), ("Comment", "comment"),
    ("ID", "reviewId"),
]

location = typer.Typer(help="The locations the Google account manages.")
review = typer.Typer(help="Reviews of a location, and the business's reply.")


# --------------------------------------------------------------------------
# location
# --------------------------------------------------------------------------

@location.command("list", help="List every location the account manages, with its Place ID.")
def location_list(output_json: bool = _JSON):
    sess = _session()
    try:
        items = sess.all_locations()
    except BusinessProfileError as e:
        typer.echo(f"Error fetching locations: {e}", err=True)
        raise typer.Exit(1)
    items = asof.current_state(items, "the location list")
    emit_list(items, _LOCATION_COLS, "location", output_json)


# --------------------------------------------------------------------------
# review
# --------------------------------------------------------------------------

@review.command("list", help="List a location's reviews, newest update first.")
def review_list(
    loc: Optional[str] = _LOCATION,
    since: Optional[str] = typer.Option(
        None, "--since", help="Only reviews written on or after this date, YYYY-MM-DD (local)."),
    unreplied: bool = typer.Option(
        False, "--unreplied", help="Only reviews awaiting the business's reply."),
    all_: bool = typer.Option(False, "--all", help="Every review, however many pages."),
    limit: int = typer.Option(25, "--limit", help="Maximum reviews to keep (ignored with --all)."),
    output_json: bool = _JSON,
):
    sess = _session()
    resource = _resolve(sess, loc)
    since_dt = None
    if since:
        asof.check_window_start(to_utc_iso(since, tz=_zone()), "--since")
        since_dt = parse_iso_utc(to_utc_iso(since, tz=_zone()))
    try:
        items = sess.reviews(resource, since=since_dt, unreplied=unreplied,
                             limit=None if all_ else limit)
    except BusinessProfileError as e:
        typer.echo(f"Error fetching reviews: {e}", err=True)
        raise typer.Exit(1)
    items = asof.bound_records(items, created="createTime", modified="updateTime", what="review")
    emit_list(items, _REVIEW_COLS, "review", output_json)


@review.command("get", help="Show one review, with the business's reply if any.")
def review_get(
    review_id: str = typer.Argument(..., help="Review id (the ID column of `review list`)."),
    loc: Optional[str] = _LOCATION,
    output_json: bool = _JSON,
):
    sess = _session()
    resource = _resolve(sess, loc)
    try:
        rec = sess.review(resource, review_id)
    except BusinessProfileError as e:
        typer.echo(f"Error fetching review {review_id}: {e}", err=True)
        raise typer.Exit(1)
    rec = asof.check_record(rec, "createTime", "updateTime", what="review")
    if output_json:
        emit_record(rec, True)
        return
    emit_record({
        "id": rec.get("reviewId"),
        "written": format_local(rec.get("createTime"), tz=_zone()),
        "updated": format_local(rec.get("updateTime"), tz=_zone()),
        "stars": stars(rec),
        "reviewer": _reviewer(rec),
        "comment": rec.get("comment") or "",
        "replied": "yes" if replied(rec) else "no",
        "reply": _reply_text(rec),
        "reply_state": (rec.get("reviewReply") or {}).get("reviewReplyState") or "",
    }, False)


@review.command("reply", help="Write the business's public reply to a review (replaces an existing one).")
def review_reply(
    review_id: str = typer.Argument(..., help="Review id."),
    message: str = typer.Option(..., "--message", "-m", help="The reply text."),
    loc: Optional[str] = _LOCATION,
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    resource = _resolve(sess, loc)
    do_write(
        lambda: sess.reply(resource, review_id, message), f"reply to review {review_id}",
        confirm=f"Publish this reply on review {review_id} as the business? (goes live)",
        yes=yes, output_json=output_json)


@review.command("unreply", help="Remove the business's reply from a review.")
def review_unreply(
    review_id: str = typer.Argument(..., help="Review id."),
    loc: Optional[str] = _LOCATION,
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    resource = _resolve(sess, loc)
    do_write(
        lambda: sess.delete_reply(resource, review_id), f"remove the reply on review {review_id}",
        confirm=f"Remove the business's reply from review {review_id}?",
        yes=yes, output_json=output_json)
