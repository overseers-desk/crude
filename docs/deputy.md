# Deputy client: command surface and API boundary

Reference for `crude-deputy`. Source: Deputy's V1 Resource API, documented at `https://developer.deputy.com/` (the reference pages under `/reference/`, one per object and verb; `https://developer.deputy.com/llms.txt` indexes them). It is a documented public API over a permanent token, so this document covers the CLI surface, the error shape, and the boundary of what the API keeps, rather than a reverse-engineered protocol. Everything stated about Deputy's behaviour below was checked against a live install in October 2026.

---

## 1. Configuration

One `[deputy]` block in `~/.config/crude/config.toml`:

```toml
[deputy]
deputy_api_token = "your-permanent-token"   # Deputy: Business settings > Integrations > Permanent token
deputy_install = "yourinstall"              # the subdomain of https://yourinstall.au.deputy.com
deputy_geo = "au"                           # the region segment of the same URL
timezone = "Australia/Brisbane"             # optional; the zone shift times are shown in
```

A second account lives in a `[deputy.<name>]` subtable and is selected with `--account/-a <name>` (or `$CRUDE_ACCOUNT`). There is no login step; `crude-deputy me` shows the token's owner and so checks the credentials.

## 2. Transport and error shape

Base `https://{install}.{geo}.deputy.com/api/v1`, the token as a Bearer header, `dp-meta-option: none` to drop the response envelope. Every object is reached as `/resource/{Object}` with the same verbs: `GET /{id}`, `POST /QUERY` (search, sort, join, start, max), `GET /INFO` (fields, joins), `GET /{id}/HISTORY`, and for the editable objects `POST` to create, `POST /{id}` to update, `DELETE /{id}`. QUERY serves at most 500 records a call; `--all` walks it in pages of 500. The plain `GET /resource/{Object}` list ignores `start` and `max`, so every list in crude goes through QUERY.

An error is a non-2xx status with a body `{"error": {"code": ..., "message": ...}}`, surfaced as `Deputy API error: <message>`. Two messages need reading with care:

- `Unauthorized resource requested!` is Deputy's one answer for an object name it does not know and for an object the token may not read. A probe for a guessed name (`resource info AuditLog`) cannot tell the two apart, so crude appends that note to the message. The object names are in the API reference, not guessable.
- `Object not found` on get or history means the id names no live record: it was deleted, or never existed. Deputy does not serve a deleted record by any read (see §4).

`Access to object denied` on `employee get` has been seen for a number that was an Employee's `UserId`, not its `Id`: the two are different numbers, and `employee list --json` carries both.

## 3. Command surface

| Resource | Verbs |
|----------|-------|
| `employee` | list *(`--all`, `--ldif`)*, get *(`--ldif`)*, history |
| `roster` | list *(`--from`, `--to`, `--area`, `--employee`, `--all`)*, get, history |
| `area` | list *(`--all`)*, get, history *(area == OperationalUnit)* |
| `timesheet` | list *(`--from`, `--to`, `--employee`, `--all`)*, get, history |
| `leave` | list *(`--employee`, `--all`)*, get, history |
| `resource <Object>` | list, get, history, query *(`--where`, `--sort`, `--join`, `--json-query`, `--all`)*, info, create, update, delete |
| *(token)* | `me` |

`--json` on any read prints the raw structure. Tables show a shift's `StartTime` and `EndTime` (Unix seconds in the API) as clock times in the configured timezone and a business `Date` as the day. `--where` takes `Field op value` with op one of eq ne gt ge lt le lk nk in nn is ns; `in`/`nn` take a comma-separated value. Writes take a JSON body from `--data`, `-f`, or stdin; `delete` prompts unless `--yes`.

## 4. What Deputy keeps

**Every live record has a change log.** `GET /resource/{Object}/{id}/HISTORY` returns one row per field change:

```json
{"Creator": "127", "CreatorName": "Jane Manager", "DtChanged": "2026-10-16T19:34:20+10:00",
 "Field": "StartTime", "OldValue": "1792791000", "NewValue": "1792792800", "RecId": "12198"}
```

`crude-deputy roster history <id>` (and the sibling verbs) prints these oldest first; the table shows the stamp and a StartTime/EndTime old and new value as clock times in the configured timezone, and `--json` is raw. The record's own `Modified` field is only the time of its last write: a shift's `Modified` equals its timesheet's creation time once the employee clocks in, and says nothing of the edits before it. The question "when was this shift's start moved later, and by whom" is answered by `history`, never by `Modified`.

The API also serves a record as it stood at a given moment, `GET /resource/{Object}/{id}/HISTORY/travel/{unix-seconds}`; crude does not wire it, since the change log answers the same question.

**A deleted record is gone from every read.** For a deleted id, get, QUERY (with any clause), HISTORY and HISTORY/travel all answer `Object not found`, and Deputy's management V2 API (`/api/management/v2/shifts/{id}/history` and `/log`) answers 404 or 500. No object the API serves records a deletion: `SystemUsageTracking` has no delete event type, `Webhook` is only ever forward-looking, and `Notification` is not readable by a permanent token. The deletion's time and actor are in Deputy's web Shift Audit report, which needs the Analytics+ subscription and has no API.

What survives of a deleted shift is its publish events. `SystemUsageTracking` logs one row per publish (`UsageType` 11, description "Roster published") carrying the shift id as `UsageRecordId`, the employee as `EmpId`, the shift's `Date`, and the publisher as `Creator`, so

```
crude-deputy resource query SystemUsageTracking --where "UsageType eq 11" --where "Created ge 2026-10-16" --all --json
```

tells, for an id that get no longer serves, whose shift it was, for which day, and who published it when. A shift created and deleted without ever being published leaves nothing. `SmsLog` holds the text of each roster SMS Deputy sent, which names a deleted shift as `*DELETED*` when a republish followed the deletion and the employee receives SMS.

## 5. WORLD_AS_OF

Under the bound (`crude_common.asof`), every QUERY carries a server-side `Created le <bound>` clause and the client drops created-after records again and flags `Modified > bound`. `history` drops a change whose `DtChanged` is after the bound: a change made after the cutoff did not exist in the bounded world. Writes refuse.
