# crude-meta (graph.facebook.com)

`crude-meta` reads and edits a Facebook Page and an Instagram Business account
through the Meta Graph API. It is one binary for both surfaces because Meta is one
site: one app, one token, one host (`graph.facebook.com`). Instagram Business is
reached only through the same app and a linked Page, so it is a product surface of
Meta rather than a separate site. The platform is the first resource word:
`crude-meta instagram ...` and `crude-meta facebook ...`.

## Configuration

In `~/.config/crude/config.toml`:

```toml
[meta]
access_token = "your-meta-graph-api-token"
page_id = "..."      # the Facebook Page id
ig_user_id = "..."   # the Instagram Business account id
# app_secret = "..."  # optional; when set, every call carries an appsecret_proof
```

There is no login step. Which token to use depends on how the Page is held:

- **Page managed in Business Manager (the common case).** `/me/accounts` returns
  nothing for a user token, and a plain user token can neither read the Page nor
  mint a Page token. Use a Business **System User token** with the Page and the
  Instagram account assigned, and set `page_id` and `ig_user_id` explicitly. The
  configured token is then used directly as the Page token for every call.
- **Page held by a classic Page role.** A user token works: leave `page_id` and
  `ig_user_id` out, and the session resolves them, and a per-Page token, from
  `GET /me/accounts`.

If the Instagram account is not linked to the Page (the Page's
`instagram_business_account` is null), `ig_user_id` cannot be derived from the
Page; set it explicitly. `crude-meta account show` reports the resolved ids.

## Acquiring a token

### System User token (Business-managed Pages; also durable)

1. Meta Business Settings (`business.facebook.com/settings`) → Users → System
   users → Add; name it, role Admin.
2. Add assets to the system user: the Page (full control) and the Instagram
   account (full control); assign your app.
3. Generate a token against the app with the scopes you need (see the tables
   below); set expiry to "Never" for unattended use.
4. Put it in `[meta] access_token`, with `page_id` and `ig_user_id`.

A System User token can be non-expiring, so there is no refresh to run.

### Long-lived user token (classic-role Pages)

A Graph API Explorer token is short-lived (about an hour). Exchange it for a
~60-day token, after which `/me/accounts` yields a per-Page token:

```
GET /oauth/access_token?grant_type=fb_exchange_token
    &client_id={app-id}&client_secret={app-secret}
    &fb_exchange_token={short-lived-token}
```

A Page token derived from a long-lived user token does not expire by time (only on
password change, app removal, or loss of the Page role).

`app_secret` is optional. When set, crude sends an `appsecret_proof` (an
HMAC-SHA256 of the token keyed by the app secret) on every call, required if the
app has "Require App Secret" enabled.

## Access level

On the venue's **own** Page and Instagram account, the whole surface below runs
under Standard Access with **no App Review**, as long as the operator holds an
admin role on the assets and is an admin, developer, or tester of the app. App
Review (Advanced Access) is only for reaching assets you do not own; the few
capabilities that always need it (hashtag search, business discovery, production
messaging) are out of scope here.

## Command surface

```
crude-meta account show                      # resolved page_id, page name, ig_user_id
crude-meta status                            # token check + Instagram publishing quota

crude-meta instagram media list [--limit N]
crude-meta instagram media get <id>          # returns id and shortcode together
crude-meta instagram media insights <id> [--metric ...]
crude-meta instagram media publish --type image|video|reel|carousel|story --url <u> [--url ...] [--caption <s>] [--yes]
crude-meta instagram media delete <id> [--yes]
crude-meta instagram comment list <media-id>
crude-meta instagram comment reply <comment-id> -m <s>
crude-meta instagram comment hide|unhide|delete <comment-id>
crude-meta instagram comment toggle <media-id> --enabled|--disabled
crude-meta instagram account get
crude-meta instagram account insights [--metric reach,views,total_interactions] [--period day]

crude-meta facebook post list [--scheduled] [--limit N]
crude-meta facebook post get <id>
crude-meta facebook post insights <id> [--metric ...]
crude-meta facebook post create [-m <s>] [--link <u>] [--photo-url <u>] [--schedule <time>] [--yes]
crude-meta facebook post edit <id> -m <s> [--yes]
crude-meta facebook post delete <id> [--yes]
crude-meta facebook comment list <post-id>
crude-meta facebook comment reply <object-id> -m <s>
crude-meta facebook comment hide|unhide|delete <comment-id>
crude-meta facebook page get
crude-meta facebook page insights [--metric ...] [--period day]
```

Add `--json` to any read for the raw Graph object. Writes prompt before mutating
unless `--yes`. `media publish` for a video or reel waits for the container to
finish processing before publishing; a carousel takes two or more `--url` items.

## What the API allows

### Read

| Capability | Endpoint | Permission |
|---|---|---|
| Instagram profile | `GET /{ig-user-id}` | `instagram_basic` |
| Instagram media list/get | `GET /{ig-user-id}/media`, `GET /{media-id}` | `instagram_basic` |
| Instagram media insights | `GET /{media-id}/insights` | `instagram_manage_insights` |
| Instagram account insights | `GET /{ig-user-id}/insights` | `instagram_manage_insights` |
| Instagram comments | `GET /{media-id}/comments` | `instagram_manage_comments` |
| Page profile | `GET /{page-id}` | `pages_read_engagement` |
| Page posts | `GET /{page-id}/published_posts`, `/scheduled_posts` | `pages_read_engagement` |
| Page post + insights | `GET /{post-id}`, `/insights` | `pages_read_engagement`, `read_insights` |
| Page comments | `GET /{post-id}/comments` | `pages_read_engagement` |

### Write

| Capability | Endpoint | Permission |
|---|---|---|
| Instagram publish | `POST /{ig-user-id}/media` then `/media_publish` | `instagram_content_publish` |
| Instagram media delete | `DELETE /{media-id}` | `instagram_manage_contents` |
| Instagram comment moderate | replies, `hide`, delete, `comment_enabled` | `instagram_manage_comments` |
| Page post create/schedule | `POST /{page-id}/feed`, `/photos` | `pages_manage_posts` |
| Page post edit (message only) | `POST /{post-id}` | `pages_manage_posts` |
| Page post delete | `DELETE /{post-id}` | `pages_manage_posts` |
| Page comment moderate | comment, `is_hidden`, delete | `pages_manage_engagement` |

## Constraints worth knowing

- **Instagram captions cannot be edited** through the API. To change a caption,
  delete the media and publish afresh. There is therefore no `media edit`.
- **Facebook post editing is message-only**, and only on posts this app created
  (a post made in Meta Business Suite or another app is not editable or deletable
  by this one). The same app-created constraint applies to `post delete`.
- **Events are not reachable.** The Page events edge is restricted to Facebook
  Marketing Partners, and event creation via the API is unsupported, so there is
  no `event` command.
- **Insight metric names shift between Graph versions.** `impressions` was retired
  in favour of `views`, and `page_fans` in favour of `page_follows`. The insight
  commands carry sensible defaults and take `--metric` to override when Meta moves
  them again. Per-post Instagram insights are retained about two years;
  account-level demographics are limited to roughly a 90-day window.
- **Rate limits.** Page and Instagram calls fall under Meta's Business Use Case
  limits; Instagram content publishing has its own rolling 24-hour post cap.
  `crude-meta status` reads the current publishing quota.

## Verification

With a valid token in `[meta] access_token`:

```
crude-meta account show                            # resolves page_id and ig_user_id
crude-meta instagram media insights <17-digit id>  # per-post metrics
crude-meta instagram media get <id> --json         # id and shortcode present
crude-meta facebook post create -m "test"          # then post edit / post delete
```

Missing-token and expired-token (Graph code 190) errors print a single clean line,
not a traceback.
