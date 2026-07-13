"""ATDW API client — requests-based, bearer auth, LoopBack filter construction."""

from __future__ import annotations

import json
from pathlib import Path

from crude_common import asof
from crude_common.config import account
from crude_common.httpapi import HttpSession
from crude_common.statestore import atomic_write, state_path

API_BASE = "https://atlas.atdw-online.com.au/api"
ORG_ID = "656826d85c376a10511493fd"


def token_path() -> Path:
    """The durable JWT cache file, namespaced by the selected account.

    Lives under ``$XDG_STATE_HOME/crude`` (see ``crude_common.statestore``). The
    default account keeps the bare ``atdw_token`` name; a named account gets a
    suffix so two accounts never read each other's token.
    """
    return state_path("atdw_token", account())


class ATDWClient(HttpSession):
    def __init__(self, token: str, credentials: dict = None):
        super().__init__(API_BASE)
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        self._credentials = credentials or {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_token(self, token: str) -> None:
        """Replace the bearer token in the session and persist it durably."""
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        atomic_write(token_path(), token)

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

    def _on_401(self, r) -> bool:
        """Re-authenticate from stored credentials and retry once, if possible."""
        return self._try_refresh()

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
        result = self._post("/listings/filter", json={"filter": filter_obj})
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

    def create_listing(self, body: dict) -> dict:
        """POST /api/listings — create a new listing from a full object.

        ATDW requires at least listingType, category, owningOrganisation, name,
        and physicalAddress; a created listing starts as a draft and is not
        distributed until submit(). Refuses under WORLD_AS_OF, as do all the
        write verbs below: ATDW writes do not pass through writeio.do_write,
        so the gate lives here.
        """
        asof.guard_write("create a listing")
        return self._post("/listings", json=body)

    def patch_listing(self, listing_id: str, fields: dict) -> dict:
        """PATCH a listing with only the changed fields."""
        asof.guard_write(f"update listing {listing_id}")
        return self._patch(f"/listings/{listing_id}", json=fields)

    # ------------------------------------------------------------------
    # Sub-resource methods (programmatic use, not exposed as CLI commands)
    # ------------------------------------------------------------------

    def submit(self, listing_id: str) -> dict:
        """POST /api/listings/:id/submit — submit a listing for review."""
        asof.guard_write(f"submit listing {listing_id}")
        return self._post(f"/listings/{listing_id}/submit", json={})

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
        asof.guard_write(f"tag listing {listing_id}")
        return self._post(f"/listings/{listing_id}/tags/{tag_id}", json={})

    def remove_tag(self, listing_id: str, tag_id: str) -> dict:
        """DELETE /api/listings/:id/tags/:tagId — remove a tag from a listing."""
        asof.guard_write(f"untag listing {listing_id}")
        return self._delete(f"/listings/{listing_id}/tags/{tag_id}")
