"""Facebook Page resources for crude-facebook: posts, comments, page.

Explicit command groups, mounted directly on the root app (the grammar is
``crude-facebook <resource> <verb>``). Post editing is message-only and works only
on posts this app created (a Graph constraint), noted in the command help. The Page
events edge is omitted entirely: it is restricted to Facebook Marketing Partners
and event creation via the API is unsupported, so there is nothing a self-serve
client can do there.
"""

from __future__ import annotations

from typing import Optional

import typer

from crude_common import asof
from crude_common.output import emit_list, emit_record
from crude_common.writeio import do_write
from crude_facebook.client import (
    FB_COMMENT_FIELDS,
    FB_PAGE_FIELDS,
    FB_POST_FIELDS,
    FacebookError,
    insight_rows,
)

_JSON = typer.Option(False, "--json", help="Print the raw JSON of the result.")
_YES = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt.")

_POST_COLS = [
    ("ID", "id"), ("Posted", "created_time"), ("Message", "message"),
    ("Permalink", "permalink_url"),
]
_COMMENT_COLS = [
    ("ID", "id"), ("From", lambda c: (c.get("from") or {}).get("name")),
    ("Message", "message"), ("Posted", "created_time"),
    ("Likes", "like_count"), ("Hidden", "is_hidden"),
]
_INSIGHT_COLS = [("Metric", "metric"), ("Value", "value"), ("Title", "title")]


def _session():
    from crude_facebook.cli import _session as impl

    return impl()


post = typer.Typer(help="Facebook Page posts: list, get, insights, create, edit, delete.")
comment = typer.Typer(help="Facebook Page comment moderation.")
page = typer.Typer(help="Facebook Page profile and insights.")


# --------------------------------------------------------------------------
# post
# --------------------------------------------------------------------------

@post.command("list", help="List the Page's published posts (or scheduled with --scheduled).")
def post_list(
    scheduled: bool = typer.Option(
        False, "--scheduled", help="List future-dated scheduled posts instead."),
    limit: int = typer.Option(25, "--limit", help="Maximum posts to fetch."),
    output_json: bool = _JSON,
):
    if scheduled and asof.active():
        # A scheduled post is future-dated by nature; none existed at the cutoff.
        asof.refuse("scheduled posts are future-dated by nature")
    sess = _session()
    edge = "scheduled_posts" if scheduled else "published_posts"
    bound = asof.world_as_of()
    try:
        if bound is None:
            items = list(sess.iter_edge(
                f"/{sess.page_id}/{edge}", params={"fields": FB_POST_FIELDS}, max_items=limit))
        else:
            # Server filter (`until`) plus belt-and-braces on created_time.
            # The feed edge is reverse-chronological, so posts newer than the
            # bound are dropped while walking down until `limit` old-enough
            # posts are collected — no early stop on the drop side, or a run
            # of post-cutoff posts would starve the result.
            params = {"fields": FB_POST_FIELDS, "until": str(asof.bound_s())}
            items, dropped = [], 0
            for item in sess.iter_edge(f"/{sess.page_id}/{edge}", params=params):
                created = asof.parse_stamp(item.get("created_time"))
                if created is not None and created > bound:
                    dropped += 1
                    continue
                items.append(item)
                if len(items) >= limit:
                    break
            asof.emit_notice("post", dropped, 0)
    except FacebookError as e:
        typer.echo(f"Error fetching posts: {e}", err=True)
        raise typer.Exit(1)
    emit_list(items, _POST_COLS, "post", output_json)


@post.command("get", help="Show one post by id.")
def post_get(
    post_id: str = typer.Argument(..., help="Post id."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        rec = sess.get(f"/{post_id}", params={"fields": FB_POST_FIELDS})
    except FacebookError as e:
        typer.echo(f"Error fetching post {post_id}: {e}", err=True)
        raise typer.Exit(1)
    rec = asof.check_record(rec, "created_time", what="post")
    emit_record(rec, output_json)


@post.command("insights", help="Per-post insights (pass --metric; names shift between versions).")
def post_insights(
    post_id: str = typer.Argument(..., help="Post id."),
    metric: str = typer.Option(
        "post_reactions_by_type_total,post_clicks", "--metric",
        help="Comma-separated metrics."),
    output_json: bool = _JSON,
):
    if asof.active():
        # Insights are rolling aggregates recomputed over current data with no
        # per-row timestamp: a count fetched today is not the count as of the
        # cutoff, and no flag fixes a number.
        asof.refuse("insights are recomputed aggregates with no as-of form")
    sess = _session()
    try:
        data = sess.get(f"/{post_id}/insights", params={"metric": metric}).get("data", [])
    except FacebookError as e:
        typer.echo(f"Error fetching insights for {post_id}: {e}", err=True)
        raise typer.Exit(1)
    emit_list(insight_rows(data), _INSIGHT_COLS, "metric", output_json)


@post.command("create", help="Publish a Page post. GOES LIVE unless --schedule is given.")
def post_create(
    message: Optional[str] = typer.Option(None, "--message", "-m", help="Post text."),
    link: Optional[str] = typer.Option(None, "--link", help="A URL to attach."),
    photo_url: Optional[str] = typer.Option(
        None, "--photo-url", help="A public image URL to post as a photo."),
    schedule: Optional[str] = typer.Option(
        None, "--schedule",
        help="Unix time or ISO 8601; schedules the post (10 min to ~75 days ahead) "
             "instead of publishing now."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    if not (message or link or photo_url):
        typer.echo("Error: provide --message, --link, or --photo-url.", err=True)
        raise typer.Exit(1)

    def action():
        params = {}
        if message:
            params["message"] = message
        if link:
            params["link"] = link
        if schedule:
            params["published"] = "false"
            params["scheduled_publish_time"] = schedule
        if photo_url:
            params["url"] = photo_url
            return sess.post(f"/{sess.page_id}/photos", params=params)
        return sess.post(f"/{sess.page_id}/feed", params=params)

    do_write(
        action, "create post",
        confirm="Publish this post to the Page? (goes live)" if not schedule
        else "Schedule this post on the Page?",
        yes=yes, output_json=output_json)


@post.command(
    "edit",
    help="Edit a post's message; the message is the only field this changes (a post's "
         "photo or link cannot be swapped). With a System User token that has full Page "
         "control, it edits any of the Page's posts, not only ones this app created.")
def post_edit(
    post_id: str = typer.Argument(..., help="Post id."),
    message: str = typer.Option(..., "--message", "-m", help="New post text."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    do_write(
        lambda: sess.post(f"/{post_id}", params={"message": message}),
        f"edit post {post_id}",
        confirm=f"Edit post {post_id}? (changes the live post)",
        yes=yes, output_json=output_json)


@post.command("delete", help="Delete a post by id. Irreversible.")
def post_delete(
    post_id: str = typer.Argument(..., help="Post id to delete."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    do_write(
        lambda: sess.delete(f"/{post_id}"),
        f"delete post {post_id}",
        confirm=f"Delete post {post_id}? (cannot be undone)",
        yes=yes, output_json=output_json)


# --------------------------------------------------------------------------
# comment
# --------------------------------------------------------------------------

@comment.command("list", help="List comments on a post.")
def comment_list(
    post_id: str = typer.Argument(..., help="Post id."),
    output_json: bool = _JSON,
):
    sess = _session()
    try:
        items = list(sess.iter_edge(
            f"/{post_id}/comments", params={"fields": FB_COMMENT_FIELDS}))
    except FacebookError as e:
        typer.echo(f"Error fetching comments for {post_id}: {e}", err=True)
        raise typer.Exit(1)
    # Fetch-then-drop on created_time: the edge offers no ordering guarantee
    # worth an early stop, and correctness beats a page of extra reads.
    items = asof.bound_records(items, "created_time", what="comment")
    emit_list(items, _COMMENT_COLS, "comment", output_json)


@comment.command("reply", help="Comment as the Page on a post or reply to a comment.")
def comment_reply(
    object_id: str = typer.Argument(..., help="Post id (to comment) or comment id (to reply)."),
    message: str = typer.Option(..., "--message", "-m", help="Comment text."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    do_write(
        lambda: sess.post(f"/{object_id}/comments", params={"message": message}),
        f"comment on {object_id}", yes=yes, output_json=output_json)


@comment.command(
    "hide",
    help="Hide a visitor's comment. A Page cannot hide its own comments (Graph "
         "rejects it); this applies to comments left by others.")
def comment_hide(
    comment_id: str = typer.Argument(..., help="Comment id (a visitor's comment)."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    do_write(
        lambda: sess.post(f"/{comment_id}", params={"is_hidden": "true"}),
        f"hide comment {comment_id}", yes=yes, output_json=output_json)


@comment.command("unhide", help="Unhide a previously hidden visitor comment.")
def comment_unhide(
    comment_id: str = typer.Argument(..., help="Comment id."),
    yes: bool = _YES,
    output_json: bool = _JSON,
):
    sess = _session()
    do_write(
        lambda: sess.post(f"/{comment_id}", params={"is_hidden": "false"}),
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


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------

@page.command("get", help="Show the Page profile (followers, category, contact).")
def page_get(output_json: bool = _JSON):
    sess = _session()
    try:
        rec = sess.get(f"/{sess.page_id}", params={"fields": FB_PAGE_FIELDS})
    except FacebookError as e:
        typer.echo(f"Error fetching page: {e}", err=True)
        raise typer.Exit(1)
    # Follower counts and the like are now-values: served, disclosed as such.
    rec = asof.current_state(rec, "the Page profile (follower counts are now-values)")
    emit_record(rec, output_json)


@page.command("insights", help="Page-level insights (pass --metric; names shift between versions).")
def page_insights(
    metric: str = typer.Option(
        "page_post_engagements,page_follows", "--metric", help="Comma-separated metrics."),
    period: str = typer.Option("day", "--period", help="day, week, or days_28."),
    output_json: bool = _JSON,
):
    if asof.active():
        asof.refuse("insights are recomputed aggregates with no as-of form")
    sess = _session()
    try:
        data = sess.get(
            f"/{sess.page_id}/insights",
            params={"metric": metric, "period": period},
        ).get("data", [])
    except FacebookError as e:
        typer.echo(f"Error fetching page insights: {e}", err=True)
        raise typer.Exit(1)
    emit_list(insight_rows(data), _INSIGHT_COLS, "metric", output_json)
