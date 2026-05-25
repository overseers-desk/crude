"""ATDW API client — requests-based, bearer auth, LoopBack filter construction."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import requests

API_BASE = "https://atlas.atdw-online.com.au/api"
ORG_ID = "656826d85c376a10511493fd"
TOKEN_PATH = Path(tempfile.gettempdir()) / "crude_atdw_token"


class ATDWClient:
    def __init__(self, token: str, credentials: dict = None):
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        self._credentials = credentials or {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_token(self, token: str) -> None:
        """Replace the bearer token in the session and persist to temp file."""
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        TOKEN_PATH.write_text(token)

    def _try_refresh(self) -> bool:
        """Attempt to re-authenticate using stored credentials. Returns True on success."""
        username = self._credentials.get("username")
        password = self._credentials.get("password")
        if not username or not password:
            return False
        from crude_atdw.auth import atdw_login
        try:
            new_token = atdw_login(username, password)
            self._update_token(new_token)
            return True
        except Exception:
            return False

    def _request(self, method: str, path: str, **kwargs):
        """Execute a request, auto-refreshing on 401."""
        url = f"{API_BASE}{path}"
        r = self.session.request(method, url, **kwargs)
        if r.status_code == 401 and self._try_refresh():
            # Retry once with the new token
            r = self.session.request(method, url, **kwargs)
        r.raise_for_status()
        return r

    def _get(self, path: str, params: dict = None):
        return self._request("GET", path, params=params).json()

    def _patch(self, path: str, body: dict) -> dict:
        return self._request("PATCH", path, json=body).json()

    def _post(self, path: str, body: dict = None):
        return self._request("POST", path, json=body or {}).json()

    def _delete(self, path: str) -> dict:
        return self._request("DELETE", path).json()

    # ------------------------------------------------------------------
    # Listings
    # ------------------------------------------------------------------

    def list_listings(self, org_id: str = ORG_ID, limit: int = 20, skip: int = 0) -> list:
        """Return listings for the given organisation (non-INACTIVE only)."""
        filter_obj = {
            "limit": limit,
            "where": {
                "and": [
                    {"owningOrganisation": org_id},
                    {"status": {"neq": "INACTIVE"}},
                    {"status": {"neq": "null"}},
                ]
            },
            "include": ["contributingOrganisation", "media", "services"],
            "scope": {"media": {"favourite": True}},
            "skip": skip,
            "order": "slug ASC",
        }
        return self._get("/listings", params={"filter": json.dumps(filter_obj)})

    def search_listings(
        self,
        where_clauses: list,
        limit: int = 20,
        skip: int = 0,
    ) -> list:
        """Search across all visible listings using a LoopBack where clause.

        Uses POST /api/listings/filter which is not restricted to the owning org.
        where_clauses is a list of dicts, each representing one condition;
        they are combined with 'and'. Example:
            [{"listingType": "tour"}, {"physicalAddress.city_suburb": "Gold Coast"}]
        """
        filter_obj = {
            "where": {"and": where_clauses},
            "limit": limit,
            "skip": skip,
            "order": "slug ASC",
        }
        result = self._post("/listings/filter", {"filter": filter_obj})
        # The endpoint returns a list directly
        if isinstance(result, list):
            return result
        return result

    def get_own_listing(self, listing_id: str) -> dict:
        """Return an owned listing with relations (admin view, including drafts).

        Only works for listings belonging to the authenticated organisation.
        For external listings, use get_published_listing().
        """
        params = {
            "filter[include][0]": "stoOrganisation",
            "filter[include][1]": "contributingOrganisation",
            "filter[include][2]": "publishedListing",
        }
        return self._get(f"/listings/{listing_id}", params=params)

    def get_published_listing(self, listing_id: str) -> dict:
        """Return any listing's published data (read-only, any authenticated user).

        Works for both owned and external listings. Returns name, description,
        productContacts, socialExternalReferences, physicalAddress, etc.
        Does not return draft content or admin-only fields.
        """
        return self._get(f"/listings/{listing_id}/publishedListing")

    def patch_listing(self, listing_id: str, fields: dict) -> dict:
        """PATCH a listing with only the changed fields."""
        return self._patch(f"/listings/{listing_id}", fields)

    # ------------------------------------------------------------------
    # Sub-resource methods (programmatic use, not exposed as CLI commands)
    # ------------------------------------------------------------------

    def submit(self, listing_id: str) -> dict:
        """POST /api/listings/:id/submit — submit a listing for review."""
        return self._post(f"/listings/{listing_id}/submit")

    def list_media(self, listing_id: str) -> list:
        """GET /api/listings/:id/media — list media (images) for a listing."""
        return self._get(f"/listings/{listing_id}/media")

    def list_services(self, listing_id: str) -> list:
        """GET /api/listings/:id/services — list services for a listing."""
        return self._get(f"/listings/{listing_id}/services")

    def list_tags(self, listing_id: str) -> list:
        """GET /api/listings/:id/tags — list tags for a listing."""
        return self._get(f"/listings/{listing_id}/tags")

    def add_tag(self, listing_id: str, tag_id: str) -> dict:
        """POST /api/listings/:id/tags/:tagId — add a tag to a listing."""
        return self._post(f"/listings/{listing_id}/tags/{tag_id}")

    def remove_tag(self, listing_id: str, tag_id: str) -> dict:
        """DELETE /api/listings/:id/tags/:tagId — remove a tag from a listing."""
        return self._delete(f"/listings/{listing_id}/tags/{tag_id}")
