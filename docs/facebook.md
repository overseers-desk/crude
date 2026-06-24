# crude-facebook (graph.facebook.com)

`crude-facebook` reads and edits a Facebook Page through the Graph API, in the
`crude-facebook <resource> <verb>` grammar. Instagram is a separate product on
Meta's own roadmap (it has its own login path, `graph.instagram.com`); a future
`crude-instagram` would cover it. The working Facebook-Login Instagram code is
preserved on the `crude-meta-instagram` branch.

## Configuration

In `~/.config/crude/config.toml`:

```toml
[facebook]
access_token = "your-facebook-graph-api-token"
page_id = "..."        # the Facebook Page id
# app_secret = "..."   # optional; when set, every call carries an appsecret_proof
```

There is no login step. On first use the session resolves the Page from
`GET /me/accounts` and prefers the per-Page token it returns. For a Page managed in
**Business Manager**, `/me/accounts` is empty and a plain user token cannot reach
the Page; use a System User token with the Page assigned and set `page_id`, and the
configured token is used directly as the Page token. `crude-facebook status`
confirms the token and prints the resolved Page id and name.

## Acquiring a token

### System User token (Business-managed Pages; also durable)

1. Meta Business Settings (`business.facebook.com/settings`) → Users → System
   users → Add; name it, role Admin.
2. Add assets to the system user: the Page (full control); under **Installed apps**,
   add your app (the final "Generate token" installs it).
3. **Generate token** against the app with the scopes you need (see the table);
   set expiry to "Never" for unattended use.
4. Put it in `[facebook] access_token`, with `page_id`.

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

On the venue's own Page the whole surface below runs under Standard Access with **no
App Review**, as long as the operator holds an admin role on the Page and is an
admin, developer, or tester of the app. App Review is only for reaching Pages you do
not own.

## Command surface

```
crude-facebook status                        # token check + resolved page id/name
crude-facebook post list [--scheduled] [--limit N]
crude-facebook post get <id>
crude-facebook post insights <id> [--metric ...]
crude-facebook post create [-m <s>] [--link <u>] [--photo-url <u>] [--schedule <time>] [--yes]
crude-facebook post edit <id> -m <s> [--yes]
crude-facebook post delete <id> [--yes]
crude-facebook comment list <post-id>
crude-facebook comment reply <object-id> -m <s>
crude-facebook comment hide|unhide|delete <comment-id>
crude-facebook page get
crude-facebook page insights [--metric ...] [--period day]
```

Add `--json` to any read for the raw Graph object. Writes prompt before mutating
unless `--yes`.

## What the API allows

### Read

| Capability | Endpoint | Permission |
|---|---|---|
| Page profile | `GET /{page-id}` | `pages_read_engagement` |
| Page posts | `GET /{page-id}/published_posts`, `/scheduled_posts` | `pages_read_engagement` |
| Post + insights | `GET /{post-id}`, `/insights` | `pages_read_engagement`, `read_insights` |
| Comments | `GET /{post-id}/comments` | `pages_read_engagement` |

### Write

| Capability | Endpoint | Permission |
|---|---|---|
| Post create / schedule | `POST /{page-id}/feed`, `/photos` | `pages_manage_posts` |
| Post edit (message only) | `POST /{post-id}` | `pages_manage_posts` |
| Post delete | `DELETE /{post-id}` | `pages_manage_posts` |
| Comment moderate | comment, `is_hidden`, delete | `pages_manage_engagement` |
| Page metadata | `POST /{page-id}` | `pages_manage_metadata` |

## Constraints worth knowing

- **Post editing is message-only**: `post edit` changes the message text; a post's
  photo or link attachment cannot be swapped. With the recommended System User token
  (full Page control), edit and delete act on any of the Page's posts, including ones
  made in Meta Business Suite (verified live against this Page). The Graph docs note
  an "only the app that created it" limit for some token types; it does not apply to a
  full-control System User token.
- **Hiding applies to visitor comments only.** A Page cannot hide its own comment
  (Graph rejects `is_hidden` on it with code 200); `comment hide`/`unhide` moderate
  comments left by others. Verified live.
- **Events are not reachable.** The Page events edge is restricted to Facebook
  Marketing Partners, and event creation via the API is unsupported.
- **Facebook Groups are not reachable.** Meta removed the Groups API from all
  versions on 22 April 2024.
- **Insight metric names shift between Graph versions.** `impressions` was retired
  in favour of `views`, and `page_fans` in favour of `page_follows`. The insight
  commands carry sensible defaults and take `--metric` to override.

## Verification

With a valid token in `[facebook] access_token` and `page_id` set:

```
crude-facebook status                 # confirms the token and resolves the Page
crude-facebook post list              # recent posts
crude-facebook page insights          # page-level metrics
```

The live tests cover every read branch and a reversible write round-trip
(`pytest -m live -k facebook`); the round-trip creates a post, edits and moderates
it, then deletes it, touching only content it makes.
Missing-token and code-190/210 errors print a single clean line, not a traceback.
