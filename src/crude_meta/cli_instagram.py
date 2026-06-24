"""Instagram sub-app for crude-meta: media, comments, account.

Explicit command groups rather than a registry factory: Instagram's resources are
few and heterogeneous (media, comments, account), so the registry abstraction
crude-clover uses for its many homogeneous resources earns nothing here. Reads
honour --json via emit_list/emit_record; writes go through writeio.do_write so
they confirm before mutating and report cleanly.
"""

from __future__ import annotations

from typing import List, Optional

import typer

from crude_common.output import emit_list, emit_record
from crude_common.writeio import do_write
from crude_meta import publishing
from crude_meta.client import (
    IG_ACCOUNT_FIELDS,
    IG_COMMENT_FIELDS,
    MEDIA_FIELDS,
    MetaError,
    insight_rows,
    media_metrics,
)

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")
_YES = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt.")

_MEDIA_COLS = [
    ("ID", "id"), ("Shortcode", "shortcode"), ("Type", "media_type"),
    ("Posted", "timestamp"), ("Permalink", "permalink"),
]
_COMMENT_COLS = [
    ("ID", "id"), ("From", "username"), ("Text", "text"),
    ("Posted", "timestamp"), ("Likes", "like_count"), ("Hidden", "hidden"),
]
_INSIGHT_COLS = [("Metric", "metric"), ("Value", "value"), ("Title", "title")]


def _session():
    from crude_meta.cli import _session as impl

    return impl()


app = typer.Typer(help="Instagram Business: media, insights, comments, account.")
media = typer.Typer(help="Instagram media: list, get, insights, publish, delete.")
comment = typer.Typer(help="Instagram comment moderation.")
account_app = typer.Typer(help="Instagram account profile and insights.")
app.add_typer(media, name="media")
app.add_typer(comment, name="comment")
app.add_typer(account_app, name="account")


# --------------------------------------------------------------------------
# media
# --------------------------------------------------------------------------

@media.command("list", help="List the account's media (most recent first).")
def media_list(
    limit: int = typer.Option(25, "--limit", help="Maximum media to fetch."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        items = list(sess.iter_edge(
            f"/{sess.ig_user_id}/media", params={"fields": MEDIA_FIELDS}, max_items=limit))
    except MetaError as e:
        typer.echo(f"Error fetching media: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, _MEDIA_COLS, "media", output_json)


@media.command("get", help="Show one media by id (returns id and shortcode together).")
def media_get(
    media_id: str = typer.Argument(..., help="Media id (the 17-digit Business Suite post id)."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        rec = sess.get(f"/{media_id}", params={"fields": MEDIA_FIELDS})
    except MetaError as e:
        typer.echo(f"Error fetching media {media_id}: {e}", err=True)
        raise typer.Exit(1)
    emit_record(rec, output_json)


@media.command("insights", help="Per-post insights; metrics chosen by media type.")
def media_insights(
    media_id: str = typer.Argument(..., help="Media id."),
    metric: Optional[str] = typer.Option(
        None, "--metric", help="Comma-separated metrics; overrides the per-type default."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        if not metric:
            mpt = sess.get(
                f"/{media_id}", params={"fields": "media_product_type"}
            ).get("media_product_type")
            metric = ",".join(media_metrics(mpt))
        data = sess.get(f"/{media_id}/insights", params={"metric": metric}).get("data", [])
    except MetaError as e:
        typer.echo(f"Error fetching insights for {media_id}: {e}", err=True)
        raise typer.Exit(1)
    emit_list(insight_rows(data), _INSIGHT_COLS, "metric", output_json)


@media.command("publish", help="Create and publish a post. GOES LIVE on Instagram.")
def media_publish(
    type_: str = typer.Option(
        ..., "--type", help="image, video, reel, carousel, or story."),
    url: List[str] = typer.Option(
        ..., "--url", help="Public media URL (repeat --url for a carousel)."),
    caption: Optional[str] = typer.Option(None, "--caption", help="Caption text."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    do_write(
        lambda: publishing.publish(sess, sess.ig_user_id, kind=type_, urls=url, caption=caption),
        f"publish {type_}",
        confirm=f"Publish this {type_} to Instagram? (goes live)",
        yes=yes, output_json=output_json)


@media.command("delete", help="Delete a media by id. Irreversible.")
def media_delete(
    media_id: str = typer.Argument(..., help="Media id to delete."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    do_write(
        lambda: sess.delete(f"/{media_id}"),
        f"delete media {media_id}",
        confirm=f"Delete media {media_id}? (cannot be undone)",
        yes=yes, output_json=output_json)


# --------------------------------------------------------------------------
# comment
# --------------------------------------------------------------------------

@comment.command("list", help="List comments on a media.")
def comment_list(
    media_id: str = typer.Argument(..., help="Media id."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        items = list(sess.iter_edge(
            f"/{media_id}/comments", params={"fields": IG_COMMENT_FIELDS}))
    except MetaError as e:
        typer.echo(f"Error fetching comments for {media_id}: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, _COMMENT_COLS, "comment", output_json)


@comment.command("reply", help="Reply to a comment as the account.")
def comment_reply(
    comment_id: str = typer.Argument(..., help="Comment id to reply to."),
    message: str = typer.Option(..., "--message", "-m", help="Reply text."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    do_write(
        lambda: sess.post(f"/{comment_id}/replies", params={"message": message}),
        f"reply to comment {comment_id}", yes=yes, output_json=output_json)


@comment.command("hide", help="Hide a comment.")
def comment_hide(
    comment_id: str = typer.Argument(..., help="Comment id."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    do_write(
        lambda: sess.post(f"/{comment_id}", params={"hide": "true"}),
        f"hide comment {comment_id}", yes=yes, output_json=output_json)


@comment.command("unhide", help="Unhide a comment.")
def comment_unhide(
    comment_id: str = typer.Argument(..., help="Comment id."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    do_write(
        lambda: sess.post(f"/{comment_id}", params={"hide": "false"}),
        f"unhide comment {comment_id}", yes=yes, output_json=output_json)


@comment.command("delete", help="Delete a comment by id. Irreversible.")
def comment_delete(
    comment_id: str = typer.Argument(..., help="Comment id to delete."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    do_write(
        lambda: sess.delete(f"/{comment_id}"),
        f"delete comment {comment_id}",
        confirm=f"Delete comment {comment_id}? (cannot be undone)",
        yes=yes, output_json=output_json)


@comment.command("toggle", help="Enable or disable commenting on a media.")
def comment_toggle(
    media_id: str = typer.Argument(..., help="Media id."),
    enabled: bool = typer.Option(
        ..., "--enabled/--disabled", help="Allow or block new comments."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    state = "true" if enabled else "false"
    do_write(
        lambda: sess.post(f"/{media_id}", params={"comment_enabled": state}),
        f"set comment_enabled={state} on {media_id}", yes=yes, output_json=output_json)


# --------------------------------------------------------------------------
# account
# --------------------------------------------------------------------------

@account_app.command("get", help="Show the account profile (followers, media count, bio).")
def account_get(output_json: bool = _JSON):
    sess = _session()
    try:
        rec = sess.get(f"/{sess.ig_user_id}", params={"fields": IG_ACCOUNT_FIELDS})
    except MetaError as e:
        typer.echo(f"Error fetching account: {e}", err=True)
        raise typer.Exit(1)
    emit_record(rec, output_json)


@account_app.command("insights", help="Account-level insights (reach, views, interactions).")
def account_insights(
    metric: str = typer.Option(
        "reach,views,total_interactions", "--metric", help="Comma-separated metrics."),
    period: str = typer.Option("day", "--period", help="day, week, or days_28."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        data = sess.get(
            f"/{sess.ig_user_id}/insights",
            params={"metric": metric, "period": period, "metric_type": "total_value"},
        ).get("data", [])
    except MetaError as e:
        typer.echo(f"Error fetching account insights: {e}", err=True)
        raise typer.Exit(1)
    emit_list(insight_rows(data), _INSIGHT_COLS, "metric", output_json)
