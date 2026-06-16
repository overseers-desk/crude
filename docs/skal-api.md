# Skål Australia API Reference (Reverse-Engineered)

Discovery date: 2026-03-26. Source: `australia.skal.org` Odoo 12.0 portal.

## Architecture

- **Platform**: Odoo 12.0 (`skal12_migracion` database), hosted at `australia.skal.org`
- **API endpoint**: `POST /web/dataset/call_kw` — Odoo JSON-RPC, exposes ORM model operations
- **Auth**: Session cookie (`session_id`), opaque 40-char hex string, ~30-day lifetime
- **Session info**: `POST /web/session/get_session_info`

## Authentication

### Login (2 HTTP calls)

```python
import re, requests

def skal_login(username, password):
    s = requests.Session()
    # Step 1: GET login page — establishes initial session_id, returns CSRF token
    r = s.get("https://australia.skal.org/web/login")
    csrf = re.search(r'name=["\']csrf_token["\'] value=["\']([^"\']+)["\']', r.text).group(1)
    # Step 2: POST credentials — server rotates session_id on success
    s.post(
        "https://australia.skal.org/web/login",
        data={"login": username, "password": password, "csrf_token": csrf, "redirect": ""},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        allow_redirects=True,
    )
    return s.cookies["session_id"]
```

All subsequent calls use: `Cookie: session_id=<token>`

### Verify session

```
POST /web/session/get_session_info
Content-Type: application/json
Body: {"jsonrpc":"2.0","method":"call","params":{}}
```

Returns `uid`, `username`, `name`, `partner_id`, `session_id`.

### Known constants

| Key | Value |
|-----|-------|
| Login URL | `https://australia.skal.org/web/login` |
| API base | `https://australia.skal.org/web/dataset/call_kw` |
| Odoo version | 12.0-20221012 |
| Australia NC ID | `1000` |
| My user ID (WEIWU ZHANG) | `197082` |
| My partner ID | `204678` |
| My member record ID | `184914` |
| My member code | `297065743446E` |

## API Call Structure

All data queries go through a single endpoint using Odoo JSON-RPC:

```
POST /web/dataset/call_kw
Content-Type: application/json
```

Body envelope:
```json
{
  "jsonrpc": "2.0",
  "method": "call",
  "params": {
    "model": "<model_name>",
    "method": "search_read",
    "args": [[<domain_filters>]],
    "kwargs": {
      "fields": ["field1", "field2"],
      "limit": 100,
      "offset": 0,
      "order": "name ASC"
    }
  }
}
```

Domain filter syntax (Odoo): `[["field", "operator", value]]`

Operators: `=`, `!=`, `<`, `>`, `<=`, `>=`, `like`, `ilike`, `in`, `not in`, `child_of`

Combine with `&` (AND, default) or `|` (OR) as prefix operators.

## Models

### `member` — Tourism industry members

Access: read-all (any authenticated user can read all records).

Key fields:
- `name` — full name (uppercase)
- `first_name`, `last_name`
- `member_code` — e.g. `297065743446E`
- `work_email`, `work_phone`, `work_mobile`
- `work_city`, `work_country_id`
- `principal_work_company` — employer
- `principal_work_position` — job title
- `entity_id` — club (many2one → `entity`)
- `national_committee_id` — NC (many2one → `entity`)
- `area_committee_id`
- `state` — `active` | `draft` | `unpaid` | `done`
- `category_type` — member category
- `gender`, `birth_date`, `start_date`, `leaving_date`
- `linkedin_url`, `facebook_url`, `twitter_url`, `instagram_url`, `skype_url`
- `image` — base64-encoded photo

Total records: ~11,681 worldwide; 743 in Australia.

#### List Australian members

```json
{
  "model": "member",
  "method": "search_read",
  "args": [[["national_committee_id", "=", 1000]]],
  "kwargs": {
    "fields": ["name", "work_email", "work_city", "entity_id", "state", "principal_work_company", "principal_work_position"],
    "limit": 100,
    "offset": 0
  }
}
```

#### Get member by email

```json
{
  "model": "member",
  "method": "search_read",
  "args": [[["work_email", "=", "someone@example.com"]]],
  "kwargs": {"fields": ["name", "member_code", "entity_id", "state"], "limit": 1}
}
```

### `entity` — Clubs, NCs, and area committees

Access: read-all.

Key fields:
- `name` — e.g. `Gold Coast`, `Australia`
- `entity_type` — `CLUB` | `NC` | `AREAC`
- `country_id`
- `parent_id` — NC for a club

#### Australian clubs (16 total)

| ID | Name |
|----|------|
| 321 | Adelaide |
| 322 | Brisbane |
| 1001 | Broome |
| 324 | Cairns |
| 325 | Canberra |
| 1002 | Darwin |
| 1003 | Gold Coast |
| 1004 | Hobart |
| 1011 | Kununurra |
| 1005 | Launceston |
| 330 | Melbourne |
| 333 | Perth |
| 336 | Sunshine Coast |
| 334 | Sydney |
| 1007 | Sydney North |
| 1009 | Sydney South |

### `event.event` — Skål events

Access: read-all. ~387 total records (historical + upcoming).

Key fields: `name`, `date_begin`, `date_end`, `description`, `location`, `organizer_id`, `state`

### `skal.benefit` — Member benefits

Access: read-all. 18 records. These are the **global** Skål International benefits register: discounts and offers federated by clubs worldwide (e.g. Choice Hotels via the General Secretariat, Paradores via Madrid, SIXT via München). Australian clubs do not enter their offers here; the Australian member-to-member discounts are hand-authored on the CMS page `/australian-members-benefits` (a website page, not a queryable model), so this resource does not surface them.

Key fields:
- `name`: offer title
- `description`: HTML body with the offer terms
- `activity_id`: industry category (many2one, e.g. `ACCOMMODATION PROVIDERS`)
- `entity_id`: club/secretariat that registered the offer (many2one → `entity`)
- `country_id`: country of the offering business (many2one)
- `website`: the offer/booking URL
- `start_date`, `end_date`: validity window
- `active`: whether the offer is live
- `image`, `logo`: base64 binaries (omitted by `crude-skal`)

#### List benefits

```json
{
  "model": "skal.benefit",
  "method": "search_read",
  "args": [[]],
  "kwargs": {
    "fields": ["name", "activity_id", "entity_id", "country_id", "website", "start_date", "end_date"],
    "limit": 50,
    "order": "name ASC"
  }
}
```

### `product.template` — Shop products

Access: read (8 records visible).

### `res.partner`

Access: own record only (returns 1 result).

### `event.registration`

Access: blocked — requires event manager role.

## Other Endpoints

### Custom Skål JSON-RPC endpoints

```
POST /json/get_member/
Body: {"member_code": "297065743446E"}
Returns: member object or []
```

```
POST /entity/get_nc
Body: {"club_id": 1003}
Returns: {"name": "Australia", "country": "Australia"}
```

```
POST /member/check_email
Body: {"email": "someone@example.com"}
Returns: {"valid": true}
```

### Member portal

```
GET  /my                              — portal home (documents, cards)
GET  /MemberFinder                    — member directory search UI
GET  /ContactMember/{partner_id}      — individual member profile
GET  /ClubDetail/{entity_id}          — club/NC detail page
GET  /BenefitFinder                   — member benefits search UI
GET  /events                          — events list

POST /member-change-details           — edit own profile (multipart/form-data)
```

### PDF reports (authenticated)

```
GET /report/pdf/skal_member_card.member_card/{member_id}
GET /report/pdf/skal_member_card.member_certificate/{member_id}
GET /report/pdf/skal_member_card.report_skal_member_letterhead/{member_id}
GET /report/pdf/skal_member_card.report_skal_member_letterfooter/{member_id}
```

## Member Data Model vs ATDW

| Concept | ATDW | Skål |
|---------|------|------|
| Primary record | Listing (place/event) | Member (person) |
| Unique ID | MongoDB ObjectId | Odoo integer ID |
| Org unit | Organisation | Club → NC |
| Status field | `status` (ACTIVE/DRAFT/…) | `state` (active/unpaid/done/…) |
| Auth type | JWT (Bearer token, ~7h) | Session cookie (~30 days) |
| Query style | LoopBack filter (POST JSON) | Odoo domain (JSON-RPC) |
| Edit method | PATCH specific fields | `write` via call_kw (not tested) |

## Notes

- Odoo 12 (2018 LTS); JSON-RPC only — no REST API
- CSRF token required only for browser form submissions; JSON-RPC calls use the session cookie only
- Session lifetime: observed ~30 days (1782285330 Unix epoch ≈ 2026-04-21 from cookie jar)
- The `session_id` rotates on each successful login
- `web.base.url` in session info returns `https://skal.org` (global), not `australia.skal.org` — the AU site is a separate Odoo instance
