# crude-business-profile (business.google.com)

`crude-business-profile` reads and acts on your own Google Business Profile
through Google's Business Profile APIs, in the
`crude-business-profile <resource> <verb>` grammar: the locations a Google
account manages, their reviews, and the business's reply to each. It reaches
only locations the signed-in account manages; another business's reviews are
not available through these APIs.

## Configuration

The credential is a token file, not a config key:
`$XDG_STATE_HOME/crude/business_profile_token.json` (default
`~/.local/state/crude/`; `business_profile_token_<account>.json` for a named
account). It is google-auth's authorised-user JSON: `refresh_token`,
`client_id`, `client_secret`, `token_uri`, `scopes`, and the last `token` with
its `expiry`. The file is written by `auth` and only read afterwards. Google
does not rotate the refresh token on use, so the same file serves another
program at the same time, and a token made elsewhere with google-auth copies
into place unchanged.

`[business-profile]` in `~/.config/crude/config.toml` is read by `auth`, for
the OAuth client, and by the review commands, for the default location:

```toml
[business-profile]
client_id = "....apps.googleusercontent.com"   # a Desktop-app OAuth client; `auth` only
client_secret = "..."
# redirect_uri = "http://localhost:8911/"      # the loopback `auth` listens on
# default_location = "Riverside Cafe"          # title substring, store code, or accounts/<id>/locations/<id>
```

## Acquiring the token

### Copy a working token

Any authorised-user token file made with google-auth for the
`https://www.googleapis.com/auth/business.manage` scope serves as it is:

```
mkdir -p ~/.local/state/crude
install -m 600 <token.json> ~/.local/state/crude/business_profile_token.json
crude-business-profile status
```

No config key is needed on this route.

### Consent once with `auth`

1. In a Google Cloud project with Business Profile API access (Google grants
   it on application; until then the quota is zero), enable the My Business
   Account Management API, the My Business Business Information API and the
   Google My Business API (the v4 one, which serves reviews).
2. Create an OAuth client of type Desktop app and put its id and secret under
   `[business-profile]`. Publish the OAuth consent screen: while it is in
   Testing, Google expires refresh tokens after seven days.
3. Run `crude-business-profile auth` and sign in as the Google account that
   manages the locations. `--no-browser` prints the consent URL instead of
   opening one; `--manual` is the paste-based flow for a box with no browser.

`auth` asks for `access_type=offline` and `prompt=consent`, so a refresh token
is issued even when the account has consented before, and writes the file in
google-auth's shape.

## Commands

```
crude-business-profile status                      # token check; the Google account and the accounts it manages
crude-business-profile location list               # title, store code, address, Place ID, resource name
crude-business-profile review list [-l <loc>] [--since YYYY-MM-DD] [--unreplied] [--all] [--limit N]
crude-business-profile review get <id> [-l <loc>]
crude-business-profile review reply <id> -m <text> [-l <loc>] [--yes]
crude-business-profile review unreply <id> [-l <loc>] [--yes]
```

`-l/--location` names the location: a case-insensitive substring of its title,
its store code, its `locations/<id>` name, or the full
`accounts/<id>/locations/<id>`. Left out, `default_location` from config is
used, and with neither, the one location the account manages; several
locations and no name is an error that lists them.

`review list` pages newest update first and stops at `--limit` (25) reviews,
or runs to the last page with `--all`. `--since`
keeps reviews written on or after the date, read as a day in the config's
`timezone` (the machine's when none is set). `--unreplied` keeps reviews
awaiting the business's reply; a reply Google rejected is not public, so that
review counts as unreplied. `review reply` replaces an existing reply.

Add `--json` to any read for Google's own record. Writes prompt unless `--yes`.

## The APIs

The hosts, all under one token:

| Call | Endpoint |
|---|---|
| accounts | `GET https://mybusinessaccountmanagement.googleapis.com/v1/accounts` (pageSize 20) |
| locations | `GET https://mybusinessbusinessinformation.googleapis.com/v1/{account}/locations?readMask=name,title,storeCode,storefrontAddress,metadata` (pageSize 100) |
| reviews | `GET https://mybusiness.googleapis.com/v4/{account}/{location}/reviews?orderBy=updateTime desc` (pageSize 50) |
| one review | `GET .../reviews/{reviewId}` |
| reply | `PUT .../reviews/{reviewId}/reply` with `{"comment": "..."}` |
| remove reply | `DELETE .../reviews/{reviewId}/reply` |

Each list follows `nextPageToken` to the end. A location's Place ID sits in
`metadata.placeId`. A review carries `reviewId`, `starRating` (`ONE` to
`FIVE`), `comment` (absent for a star-only review), `createTime`,
`updateTime`, `reviewer.displayName`, and `reviewReply` with its `comment`
when the business has answered.

The access token lasts an hour; the session refreshes it from the refresh
token before it expires and again on a 401, in memory. Nothing is written back
to the file.

## WORLD_AS_OF

Under a bound, `review list` drops reviews written after the cutoff and flags
those updated after it; `review get` refuses a review written after it. The
location list and the account list are current state, served flagged. Every
write refuses.

## Troubleshooting

| Symptom | What it means |
|---|---|
| `no Google token at ...` | The token file is missing: copy a working one there, or run `auth`. |
| `invalid_grant` on refresh | The refresh token was revoked, or expired because the consent screen is in Testing. Publish the screen and run `auth` again. |
| `Business Profile API returned 403` | Google's message names the cause: an API not enabled in the Cloud project, a quota of zero (the project lacks Business Profile API access), or an account without access to the location. |
| `the token's accounts manage no locations` | The signed-in Google account manages nothing; sign in as the owner or a manager of the profile. |
| `several locations; pass --location` | The account manages more than one; name one with `-l` or set `default_location`. |
