"""Instagram content publishing: the create-container then publish two-step.

A single image, reel, or story creates one container; a carousel creates a child
container per item then a parent that references them. Video-bearing containers
(reels, video stories, video carousel items) are processed asynchronously, so the
container's ``status_code`` is polled until ``FINISHED`` before publishing.

Caption editing has no API, so there is no edit here: to change a published
post's caption you delete it (``media delete``) and publish afresh.
"""

from __future__ import annotations

import time

from crude_meta.client import MetaError

_POLL_INTERVAL = 5  # seconds between status checks
_POLL_ATTEMPTS = 60  # ~5 minutes before giving up on a stuck container


def _is_video(url: str) -> bool:
    return url.lower().split("?")[0].endswith((".mp4", ".mov"))


def _create_container(session, ig_user_id, params) -> str:
    resp = session.post(f"/{ig_user_id}/media", params=params)
    cid = resp.get("id")
    if not cid:
        raise MetaError(f"container creation returned no id: {resp}")
    return cid


def _wait_finished(session, container_id, *, interval=_POLL_INTERVAL,
                   attempts=_POLL_ATTEMPTS) -> None:
    for _ in range(attempts):
        status = session.get(
            f"/{container_id}", params={"fields": "status_code"}).get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise MetaError(f"container {container_id} processing failed (status ERROR)")
        time.sleep(interval)
    raise MetaError(
        f"container {container_id} not FINISHED after {attempts * interval}s")


def _publish(session, ig_user_id, creation_id) -> dict:
    return session.post(
        f"/{ig_user_id}/media_publish", params={"creation_id": creation_id})


def publish(session, ig_user_id, *, kind, urls, caption=None) -> dict:
    """Create and publish one Instagram post; return the published media id."""
    kind = kind.lower()
    if kind == "carousel":
        if len(urls) < 2:
            raise MetaError("carousel needs at least two --url items")
        children = []
        for u in urls:
            child = {"is_carousel_item": "true"}
            child["video_url" if _is_video(u) else "image_url"] = u
            cid = _create_container(session, ig_user_id, child)
            if _is_video(u):
                _wait_finished(session, cid)
            children.append(cid)
        params = {"media_type": "CAROUSEL", "children": ",".join(children)}
        if caption:
            params["caption"] = caption
        parent = _create_container(session, ig_user_id, params)
        return _publish(session, ig_user_id, parent)

    if not urls:
        raise MetaError("publish needs a --url")
    url = urls[0]
    params = {}
    if caption:
        params["caption"] = caption
    if kind == "image":
        params["image_url"] = url
        cid = _create_container(session, ig_user_id, params)
    elif kind in ("video", "reel"):
        params.update({"media_type": "REELS", "video_url": url})
        cid = _create_container(session, ig_user_id, params)
        _wait_finished(session, cid)
    elif kind == "story":
        params["media_type"] = "STORIES"
        params["video_url" if _is_video(url) else "image_url"] = url
        cid = _create_container(session, ig_user_id, params)
        if _is_video(url):
            _wait_finished(session, cid)
    else:
        raise MetaError(
            f"unknown publish type {kind!r}; use image, video, reel, carousel, or story")
    return _publish(session, ig_user_id, cid)
