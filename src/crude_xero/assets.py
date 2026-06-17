"""Xero Fixed Assets API (assets.xro/1.0) method group over a XeroSession.

One method group for the Assets product. The asset list takes a `status` filter
that the API makes required (DRAFT|REGISTERED|DISPOSED) and pages via the
`page`/`pageSize` query params; its response wraps the records as
``{"pagination": {...}, "items": [...]}`` — a different shape from Accounting's
single-plural-key wrap, so it gets its own `items` unwrap here rather than reusing
`_extract_list`. Asset types come back as a bare JSON array, and settings is a
single object. Assets and asset types are create-only (the API exposes no update
or delete); settings is read-only.
"""

from __future__ import annotations

BASE = "assets"


class AssetsAPI:
    def __init__(self, session):
        self.session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _items(data):
        """Unwrap the asset records from the ``{"pagination":..., "items":[...]}`` envelope.

        The Assets list response keeps the records under `items`, with the paging
        metadata beside them under `pagination` — unlike Accounting's
        single-plural-key shape. Pull `items`, tolerating a bare list and odd
        shapes (falls back to []).
        """
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                return items
        if isinstance(data, list):
            return data
        return []

    # ------------------------------------------------------------------
    # Assets (list filtered by the required status; create-only)
    # ------------------------------------------------------------------

    def list_assets(self, status, page=None, page_size=None, order_by=None):
        """List assets in one status (DRAFT|REGISTERED|DISPOSED); status is required.

        Pages via the `page`/`pageSize` query params (the response carries the
        paging metadata under `pagination`); unset filters are dropped.
        """
        params = {"status": status, "page": page, "pageSize": page_size, "orderBy": order_by}
        params = {k: v for k, v in params.items() if v is not None}
        return self._items(self.session._get(BASE, "Assets", params=params))

    def get_asset(self, asset_id):
        return self.session._get(BASE, f"Assets/{asset_id}")

    def create_asset(self, body):
        return self.session._post(BASE, "Assets", json=body)

    # ------------------------------------------------------------------
    # Asset types (bare-array list; create)
    # ------------------------------------------------------------------

    def list_asset_types(self):
        data = self.session._get(BASE, "AssetTypes")
        return data if isinstance(data, list) else []

    def create_asset_type(self, body):
        return self.session._post(BASE, "AssetTypes", json=body)

    # ------------------------------------------------------------------
    # Settings (read-only singleton)
    # ------------------------------------------------------------------

    def get_settings(self):
        return self.session._get(BASE, "Settings")
