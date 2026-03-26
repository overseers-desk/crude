# ATDW API Reference (Reverse-Engineered)

Discovery date: 2026-03-26. Source: `www.atdw-online.com.au` AngularJS SPA + `atlas.atdw-online.com.au` LoopBack REST API.

## Architecture

- **Frontend**: AngularJS 1.x SPA at `www.atdw-online.com.au`, hash-based routing (`#/listing/...`)
- **API server**: `https://atlas.atdw-online.com.au/api` (LoopBack/Node.js)
- **Proxy server**: `https://atlas.atdw-online.com.au/proxy` (purpose unclear, possibly distribution API proxy)
- **OAuth server**: `https://oauth.atdw-online.com.au` (Express, session-based)
- **Asset CDN**: `https://assets.atdw-online.com.au/images/` (image storage with on-the-fly crop/resize)
- **Auth**: OAuth 2 implicit grant; JWT bearer tokens (RS512), stored client-side in `localStorage['LoginContext']`
- **Token lifetime**: ~7 hours (observed `exp` claim)

## Authentication

### Login (3 HTTP calls, no browser needed)

```python
import re, requests

def atdw_login(username, password):
    s = requests.Session()
    # Step 1: POST credentials
    s.post(
        "https://oauth.atdw-online.com.au/login",
        data={"username": username, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        allow_redirects=False,
    )
    # Step 2: Follow OAuth2 implicit grant
    r = s.get(
        "https://oauth.atdw-online.com.au/oauth2/authorize",
        params={
            "response_type": "token",
            "redirect_uri": "https://www.atdw-online.com.au",
            "client_id": "12349d7eb9c04d6c8613e4b5f97854f3",
            "state": "%2Fhome",
        },
        allow_redirects=False,
    )
    # Step 3: Extract JWT from redirect URL fragment
    token = re.search(r"access_token=([^&]+)", r.headers["location"]).group(1)
    return token
```

All subsequent API calls use: `Authorization: Bearer <token>`

### Known constants

| Key | Value |
|-----|-------|
| OAuth client_id | `12349d7eb9c04d6c8613e4b5f97854f3` |
| API base | `https://atlas.atdw-online.com.au/api` |
| Proxy base | `https://atlas.atdw-online.com.au/proxy` |
| Asset base | `https://assets.atdw-online.com.au/images/` |
| Google Maps key | `AIzaSyA8AVzb0VNj1AdwU4vH6zjJwjDqG6fGFgw` |

## Organisation (Historic Rivermill)

| Field | Value |
|-------|-------|
| Org ID | `656826d85c376a10511493fd` |
| Org Number | `OR0059065` |
| ABN | `67647427861` |
| User ID (Barbara) | `6568270f35cf94c13341706b` |
| Plan | Contributor / STANDARD_OPERATOR |

## Known Listings

| Listing ID | Type | Slug | Status |
|------------|------|------|--------|
| `6568273cc9320b7770116404` | foodDrink | historic-rivermill | ACTIVE |
| `6903586bb6fc9ea77eaaed84` | attraction | historic-rivermill | ACTIVE |
| `696600aa66821ba339fb2b05` | accommodation | historic-rivermill-farmstay | DRAFTINPROG |
| `69b14f64d5bb6b47750392c1` | event | cowboys-&-country | ACTIVE |
| `67891ac1f4c999b32e8d8672` | event | pisco-sour-day-... | EXPIRED |

## API Endpoints

### Listings

#### List all listings for the organisation

```
GET /api/listings?filter={"limit":10,"where":{"and":[{"owningOrganisation":"656826d85c376a10511493fd"},{"status":{"neq":"INACTIVE"}},{"status":{"neq":"null"}}]},"include":["contributingOrganisation","media","services"],"scope":{"media":{"favourite":true}},"skip":0,"order":"slug ASC"}
```

The `filter` parameter uses LoopBack filter syntax (JSON-encoded). Key filter features:
- `where`: conditions (supports `and`, `or`, `neq`, `inq`, `gt`, `lt`, etc.)
- `include`: eager-load relations (`contributingOrganisation`, `media`, `services`, `stoOrganisation`, `publishedListing`, `tags`)
- `scope`: nested filters on included relations
- `limit`, `skip`: pagination
- `order`: sort (e.g. `"slug ASC"`, `"updatedOn DESC"`)

#### Count listings

```
GET /api/listings/count?where={"and":[{"owningOrganisation":"656826d85c376a10511493fd"},{"status":{"neq":"INACTIVE"}},{"status":{"neq":"null"}}]}
```

Also available as POST: `POST /api/listings/count`

#### Query with POST (for large filters)

```
POST /api/listings/filter
Content-Type: application/json
Body: { filter object }
```

#### Get a single listing

```
GET /api/listings/:id
```

With includes:
```
GET /api/listings/:id?filter[include][0]=stoOrganisation&filter[include][1]=contributingOrganisation&filter[include][2]=publishedListing
```

#### Update listing fields (the edit operation)

```
PATCH /api/listings/:id
Content-Type: application/json
Body: { "fieldName": "new value" }
```

The frontend sends PATCH with only the changed field(s). Example: updating the description:

```json
PATCH /api/listings/6568273cc9320b7770116404
{"description": "New description text here"}
```

#### Create a listing

```
POST /api/listings
Content-Type: application/json
Body: { full listing object }
```

Required fields (based on JS code): `listingType`, `category`, `owningOrganisation`, `name`, `physicalAddress`

#### Submit listing for review

```
POST /api/listings/:id/submit
```

#### Clone a listing

```
POST /api/listings/:id/clone
```

#### Change category

```
POST /api/listings/:id/changeCategory
```

#### Export listings

```
GET /api/listings/export?filter=...
GET /api/listings/dashboardExport?filter=...
GET /api/listings/translationsExport?filter=...
```

#### Import listings

```
POST /api/listings/import?strategy=...
Content-Type: multipart/form-data
```

#### Check duplicates

```
GET /api/listings/checkDuplicates?filter=...
POST /api/listings/checkDuplicates
```

#### Bulk operations

```
POST /api/listings/bulk       (bulk tag updates)
POST /api/listings/bulkDisable
```

### Listing Services (sub-services within a listing)

```
GET    /api/listings/:id/services
POST   /api/listings/:id/services
PATCH  /api/listings/:id/services/:serviceId
DELETE /api/listings/:id/services/:serviceId
POST   /api/listings/:id/services/:serviceId/clone
```

### Media / Images

#### List media for a listing

```
GET /api/listings/:id/media
GET /api/listings/:id/media?filter[where][deleted][neq]=true
```

Response (array of media objects):
```json
{
    "kind": "image",
    "mimeType": "image/jpeg",
    "content": "https://assets.atdw-online.com.au/images/<hash>.jpeg?rect=x,y,w,h&w=2048&h=1536&rot=360",
    "thumbnail": "https://assets.atdw-online.com.au/images/<hash>.jpeg?rect=x,y,w,h&w=280&h=210&rot=360",
    "storagePath": "<hash>.jpeg",
    "metadata": {
        "altText": "...",
        "caption": "...",
        "copyright": "",
        "photographer": "",
        "ratio": "4x3",
        "aspectRatios": {
            "4x3":  {"x":..., "y":..., "width":..., "height":..., "rotate":0},
            "16x9": {"x":..., "y":..., "width":..., "height":...}
        },
        "originalDimensions": {"width": "...", "height": "..."},
        "dimen": {"width":..., "height":...}
    },
    "favourite": true,
    "deleted": false,
    "id": "...",
    "listingId": "..."
}
```

#### Upload new image to a listing

```
POST /api/listings/:id/media
Content-Type: multipart/form-data
```

Upload uses `$uploadWithProgress` — the form data includes `file` (the image file) and optionally `model` (JSON metadata).

#### Update image metadata (alt text, caption, crop, etc.)

```
PATCH /api/media/:mediaId
Content-Type: application/json
Body: { "metadata": { "altText": "...", "caption": "...", ... } }
```

#### Re-upload/replace image file

```
POST /api/media/:mediaId/upload
Content-Type: multipart/form-data
```

#### Service-level media

```
GET  /api/listings/:id/services/:serviceId/media
POST /api/listings/:id/services/:serviceId/media
```

#### Deal media

```
GET  /api/listings/:id/deals/:dealId/media
POST /api/listings/:id/deals/:dealId/media
```

### Tags

```
GET    /api/listings/:id/tags
POST   /api/listings/:id/tags/:tagId      (add tag)
DELETE /api/listings/:id/tags/:tagId      (remove tag)
```

Service-level tags:
```
GET    /api/listings/:id/services/:serviceId/tags
POST   /api/listings/:id/services/:serviceId/tags/:tagId
DELETE /api/listings/:id/services/:serviceId/tags/:tagId
```

### Related Listings

```
GET /api/listings/:id/relatedListing
GET /api/listings/:id/relatedListing/services/:serviceId
```

### Translations

```
GET    /api/listings/:id/translations
GET    /api/listings/:id/purchaseTranslations
PUT    /api/listings/:id/adminOverrideTranslations
DELETE /api/listings/:id/translations/:langCode
```

Service translations:
```
GET /api/listings/:id/services/:serviceId/translations
```

### Audit Trail

```
GET  /api/listings/:id/auditEvents?filter[include]=creator
GET  /api/listings/:id/auditEvents/count
POST /api/listings/:id/auditEvents
```

### Integrations

```
GET    /api/listings/:id/integrations
POST   /api/listings/:id/integrations
DELETE /api/listings/:id/integrations/:integrationId
```

### Organisations

```
GET /api/organisations                          (all orgs for logged-in user)
GET /api/organisations/:id                      (single org)
GET /api/organisations/:id/users                (users in org)
GET /api/organisations/:id/subscriptions        (subscription details)
```

### Users

```
GET /api/users/:id
```

### Metadata

```
GET /api/md/enumerations/default      (all enums: categories, facilities, statuses, etc.)
GET /api/md/policies/default          (access policies)
```

### Orders

```
GET /api/listings/:id/orders
```

## Listing Data Model (key fields)

Common across all listing types:
- `id`: MongoDB ObjectId
- `name`: string
- `description`: string (long text)
- `shortDescription`: string (nullable)
- `slug`: URL-friendly name
- `listingType`: `foodDrink` | `attraction` | `accommodation` | `event` | `tour` | `transport` | `hire` | `generalService` | `infoService` | `journey` | `destinationInfo`
- `category`: e.g. `RESTAURANT`, `ACCOMM`, `ATTRACTION`, `EVENT`
- `status`: `DRAFT` | `DRAFTINPROG` | `ACTIVE` | `EXPIRED` | `EXPIREDINPROG` | `INACTIVE` | ...
- `productNumber`: ATDW profile number (e.g. `AU1398373`)
- `physicalAddress`: `{type, addrLine1, city_suburb, state, postcode, country, geoCodeLocation: {lat, lng}}`
- `productContacts`: `{primaryTelephoneNumber, emailEnquiries, urlEnquiries}`
- `productBooking`: `{bookingURL, shopURL}`
- `socialExternalReferences`: `{instagramURL, facebookURL, googleMyBusinessURL}`
- `images`: array of media ObjectIds (order = display order)
- `facilities`: array of facility codes (e.g. `CARPARK`, `FAMLYFREND`, `PETALLOW`)
- `serviceTypes`: array of service type codes
- `accessibility`: `{selected, attributes, attributeBlocks}`
- `accreditations`: array
- `openingTime`: `{schedule: [{dayOfWeek, openingTime, closingTime}]}`
- `publishedExpiringOn`: ISO date (when the listing expires)
- `publishedOn`, `submittedOn`, `approvedOn`: ISO dates

Type-specific fields:
- **foodDrink**: `cuisineTypes`, `menuUrl`, `licensing`, `serviceAndPrice` (mealTypes, priceRange)
- **event**: `eventDateTime`, `eventFrequency`, `isEventFree`, `venue`
- **accommodation**: `capacity`, `checkInTime`, `checkOutTime`, `rooms`, `rates`
- **attraction**: `rates`, `memberships`
- **tour/journey**: route maps via `/services/:id/addLinestring`

## Image Asset URLs

Images are stored on `assets.atdw-online.com.au` and served with query-string crop/resize:

```
https://assets.atdw-online.com.au/images/<storagePath>?rect=x,y,w,h&w=WIDTH&h=HEIGHT&rot=ROTATION
```

- `rect=x,y,w,h`: crop rectangle
- `w=` / `h=`: output dimensions
- `rot=`: rotation in degrees
- Thumbnails use `w=280&h=210`
- Full images use `w=2048&h=1536`
- Aspect ratios `4x3` and `16x9` are pre-calculated in metadata

## Edit Pages (frontend routes)

Valid `page` values for `/listing/:type/:id/edit/:page`:

- `basic-info` (events: dates, frequency)
- `description`
- `short-description` (admin only)
- `photos`
- `contact`
- `location` (address, map)
- `opening-hours`
- `facilities`
- `services` (services and pricing)
- `deals` (deals and offers)
- `social` (social media links)
- `accessibility`
- `awards` (admin)
- `indigenous` (admin)
- `international-reach` (admin)
- `tags` (admin)
- `expiry-ownership` (admin)
- `audit-trail` (admin)
- `booking-code` (admin)
- `activities` (admin)

## Enumeration Highlights

Categories: `ACCOMM`, `ATTRACTION`, `DESTINFO`, `EVENT`, `GENSERVICE`, `HIRE`, `INFO`, `JOURNEY`, `RESTAURANT`, `TOUR`, `TRANSPORT`

Status lifecycle: `DRAFT` -> `DRAFTINPROG` (draft in progress) -> submit -> `ACTIVE` -> `EXPIRED` -> renew -> `ACTIVE`

Facilities (examples): `CARPARK`, `FAMLYFREND`, `PETALLOW`, `FUNCTIONS`, `WATERSIDE`, `OUTDOORDIN`, `ENTERTAIN`, `GALLERYMUS`, `LAWNGARDEN`

Cuisine types (examples): `AUSTRALIAN`, `COFFEE`, `HIGHTEA`, `VEGAN`, `VEGETARIAN`, `MODAUS`, `GLUTEFREE`, `WINE`, `BEER`

Accessibility levels: `DISASSIST` (actively assists), `NOASSIST` (does not currently), `DISTASSIST`

## Notes

- The API is LoopBack 2/3 (error format: `{"error":{"name":"Error","status":404,"message":"...","statusCode":404,"severity":"ERROR"}}`)
- PATCH is the primary update method (sends only changed fields, frontend saves per-field)
- The save queue (`ProcessingQueue`) ensures sequential writes — concurrent PATCHes should be serialised
- Media uploads use multipart/form-data with progress tracking
- The `connect.sid` cookie from the OAuth server has a 30-min TTL; the JWT has a 7-hour TTL
- The subscription plan never expires (expiry: `2107-02-28T00:00:00.000Z` with term `999`)
