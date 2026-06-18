"""Skål Australia Odoo JSON-RPC client."""

from __future__ import annotations

from pathlib import Path

import requests

from crude_common.config import account
from crude_common.statestore import atomic_write, state_path

API_BASE = "https://australia.skal.org"
AU_NC_ID = 1000


def session_path() -> Path:
    """The durable session-cookie cache file, namespaced by the selected account.

    Lives under ``$XDG_STATE_HOME/crude`` (see ``crude_common.statestore``). The
    default account keeps the bare ``skal_session`` name; a named account gets a
    suffix so two accounts never share a session.
    """
    return state_path("skal_session", account())


class SkalClient:
    def __init__(self, session_id: str, credentials: dict = None):
        self.session = requests.Session()
        self.session.cookies.set("session_id", session_id, domain="australia.skal.org")
        self._credentials = credentials or {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_session(self, session_id: str) -> None:
        """Replace the session cookie and persist it durably."""
        self.session.cookies.set("session_id", session_id, domain="australia.skal.org")
        atomic_write(session_path(), session_id)

    def _try_refresh(self) -> bool:
        """Attempt to re-authenticate using stored credentials. Returns True on success."""
        username = self._credentials.get("username")
        password = self._credentials.get("password")
        if not username or not password:
            return False
        from crude_skal.auth import skal_login
        try:
            new_session = skal_login(username, password)
            self._update_session(new_session)
            return True
        except Exception:
            return False

    def _call_kw(self, model: str, method: str, args: list, kwargs: dict):
        """Execute an Odoo JSON-RPC call_kw request."""
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "model": model,
                "method": method,
                "args": args,
                "kwargs": kwargs,
            },
        }
        r = self.session.post(f"{API_BASE}/web/dataset/call_kw", json=payload)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            err = data["error"]
            raise RuntimeError(f"Odoo error {err.get('code')}: {err.get('message')}")
        return data.get("result")

    @staticmethod
    def _normalise_record(record: dict) -> dict:
        """Replace Odoo's ``False`` sentinels with ``None``.

        Odoo returns ``false`` (Python ``False``) for empty many2one fields
        and unset string/date fields instead of ``null``.  Normalising here
        keeps all downstream code (JSON output, table rendering) clean.
        """
        return {k: (None if v is False else v) for k, v in record.items()}

    def _search_read(
        self,
        model: str,
        domain: list,
        fields: list,
        limit: int = 100,
        offset: int = 0,
        order: str = "name ASC",
    ) -> list:
        result = self._call_kw(
            model=model,
            method="search_read",
            args=[domain],
            kwargs={"fields": fields, "limit": limit, "offset": offset, "order": order},
        )
        if not result:
            return []
        return [self._normalise_record(r) for r in result]

    def verify_session(self) -> bool:
        """Return True if the current session_id is authenticated."""
        payload = {"jsonrpc": "2.0", "method": "call", "params": {}}
        r = self.session.post(f"{API_BASE}/web/session/get_session_info", json=payload)
        if r.status_code != 200:
            return False
        data = r.json()
        uid = data.get("result", {}).get("uid")
        return bool(uid)

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    MEMBER_LIST_FIELDS = [
        "id", "name", "work_email", "work_city", "entity_id", "state",
        "principal_work_company", "principal_work_position",
    ]

    MEMBER_DETAIL_FIELDS = [
        "id", "name", "first_name", "last_name", "member_code",
        "work_email", "work_phone", "work_mobile",
        "work_city", "work_country_id",
        "principal_work_company", "principal_work_position",
        "entity_id", "national_committee_id",
        "state", "category_type",
        "gender", "start_date", "leaving_date",
        "linkedin_url", "facebook_url", "twitter_url", "instagram_url",
    ]

    def list_members(self, limit: int = 100, offset: int = 0) -> list:
        """List current Australian members (excludes departed/done records)."""
        domain = [
            ["national_committee_id", "=", AU_NC_ID],
            ["state", "not in", ["done"]],
        ]
        return self._search_read("member", domain, self.MEMBER_LIST_FIELDS, limit=limit, offset=offset)

    def get_member(self, member_id: int) -> dict:
        """Fetch a single member by Odoo integer ID."""
        results = self._search_read(
            "member",
            [["id", "=", member_id]],
            self.MEMBER_DETAIL_FIELDS,
            limit=1,
        )
        if not results:
            raise RuntimeError(f"Member {member_id} not found.")
        return results[0]

    def search_members(self, domain: list, limit: int = 20, offset: int = 0) -> list:
        """Search members with an arbitrary Odoo domain."""
        return self._search_read("member", domain, self.MEMBER_LIST_FIELDS, limit=limit, offset=offset)

    # ------------------------------------------------------------------
    # Clubs / entities
    # ------------------------------------------------------------------

    def list_clubs(self) -> list:
        """List Australian Skål clubs (all entities whose parent is the AU NC)."""
        domain = [["parent_id", "=", AU_NC_ID]]
        fields = ["id", "name", "city", "member_count"]
        return self._search_read("entity", domain, fields, limit=50, order="name ASC")

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def list_events(self, limit: int = 20) -> list:
        """List Skål events, most recent first."""
        fields = ["id", "name", "date_begin", "date_end", "location", "state"]
        return self._search_read("event.event", [], fields, limit=limit, order="date_begin DESC")

    # ------------------------------------------------------------------
    # Benefits
    # ------------------------------------------------------------------

    # The skal.benefit model is the global Skål International benefits register
    # (offers federated by clubs worldwide), not the Australian member-to-member
    # discounts — those live on a CMS page, not in this model. Binary fields
    # (image, logo) and audit fields are deliberately left out.
    BENEFIT_LIST_FIELDS = [
        "id", "name", "activity_id", "entity_id", "country_id",
        "website", "start_date", "end_date",
    ]

    BENEFIT_DETAIL_FIELDS = [
        "id", "name", "description", "activity_id", "entity_id",
        "country_id", "website", "start_date", "end_date", "active",
    ]

    def list_benefits(self, limit: int = 50, offset: int = 0) -> list:
        """List Skål International member benefits (global, across all clubs)."""
        return self._search_read(
            "skal.benefit", [], self.BENEFIT_LIST_FIELDS, limit=limit, offset=offset
        )

    def get_benefit(self, benefit_id: int) -> dict:
        """Fetch a single benefit by Odoo integer ID."""
        results = self._search_read(
            "skal.benefit",
            [["id", "=", benefit_id]],
            self.BENEFIT_DETAIL_FIELDS,
            limit=1,
        )
        if not results:
            raise RuntimeError(f"Benefit {benefit_id} not found.")
        return results[0]
