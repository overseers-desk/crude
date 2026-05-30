"""Deputy API client — requests-based, permanent Bearer-token auth.

Deputy's Resource API is uniform across every object, so this client is generic:
one set of operations (list/get/query/info/create/update/delete) parameterised by
the object name, rather than a hand-written pair per object. The base URL is built
from the install subdomain and geo region; the token is permanent, so there is no
login or refresh.
"""

from __future__ import annotations

import requests

PAGE_MAX = 500  # Deputy's default and hard cap of records per page.


class DeputyClient:
    def __init__(self, api_token: str, install: str, geo: str):
        self.base_url = f"https://{install}.{geo}.deputy.com/api/v1"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                # Suppress the response metadata envelope, keeping bodies lean.
                "dp-meta-option": "none",
            }
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, params: dict = None, body: dict = None):
        """Issue a request and surface Deputy errors from status and body.

        Deputy reports failures with a non-2xx status and, usually, a JSON body
        of the form {"error": {"code": ..., "message": ...}}.
        """
        r = self.session.request(
            method, f"{self.base_url}{path}", params=params, json=body
        )
        if not r.ok:
            msg = ""
            try:
                data = r.json()
                err = data.get("error") if isinstance(data, dict) else None
                if isinstance(err, dict):
                    msg = err.get("message", "")
                elif isinstance(data, dict):
                    msg = data.get("message", "")
            except ValueError:
                pass
            raise RuntimeError(f"Deputy API error: {msg or f'HTTP {r.status_code}'}")
        if not r.content:
            return {}
        return r.json()

    def _get(self, path: str, params: dict = None):
        return self._request("GET", path, params=params)

    def _post(self, path: str, body: dict = None):
        return self._request("POST", path, body=body or {})

    def _delete(self, path: str):
        return self._request("DELETE", path)

    # ------------------------------------------------------------------
    # Non-resource
    # ------------------------------------------------------------------

    def me(self) -> dict:
        """Return the current token owner (GET /me)."""
        return self._get("/me")

    # ------------------------------------------------------------------
    # Generic resource operations
    # ------------------------------------------------------------------

    def list_resource(self, obj: str, start: int = 0, max_: int = PAGE_MAX) -> list:
        return self._get(f"/resource/{obj}", params={"start": start, "max": max_})

    def get_resource(self, obj: str, id: str) -> dict:
        return self._get(f"/resource/{obj}/{id}")

    def query_resource(
        self,
        obj: str,
        search: dict = None,
        sort: dict = None,
        join: list = None,
        start: int = 0,
        max_: int = PAGE_MAX,
    ) -> list:
        body = {"start": start, "max": max_}
        if search:
            body["search"] = search
        if sort:
            body["sort"] = sort
        if join:
            body["join"] = join
        return self._post(f"/resource/{obj}/QUERY", body)

    def info_resource(self, obj: str) -> dict:
        return self._get(f"/resource/{obj}/INFO")

    def create_resource(self, obj: str, data: dict) -> dict:
        return self._post(f"/resource/{obj}", data)

    def update_resource(self, obj: str, id: str, data: dict) -> dict:
        return self._post(f"/resource/{obj}/{id}", data)

    def delete_resource(self, obj: str, id: str) -> dict:
        return self._delete(f"/resource/{obj}/{id}")

    # ------------------------------------------------------------------
    # Pagination — Deputy caps a page at 500, so walk in windows
    # ------------------------------------------------------------------

    def paginate_query(
        self,
        obj: str,
        search: dict = None,
        sort: dict = None,
        join: list = None,
        page: int = PAGE_MAX,
    ) -> list:
        results, start = [], 0
        while True:
            rows = self.query_resource(
                obj, search=search, sort=sort, join=join, start=start, max_=page
            )
            results.extend(rows)
            if len(rows) < page:
                break
            start += len(rows)
        return results

    def paginate_list(self, obj: str, page: int = PAGE_MAX) -> list:
        results, start = [], 0
        while True:
            rows = self.list_resource(obj, start=start, max_=page)
            results.extend(rows)
            if len(rows) < page:
                break
            start += len(rows)
        return results
