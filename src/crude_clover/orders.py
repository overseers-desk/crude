"""Orders reads for crude-clover.

The orders endpoint filters by ``createdTime`` (UTC epoch ms) and pages by
offset, but Clover caps offset at 10000, so a busy range cannot be read in one
pass. ``day_windows`` slices a date range into one window per venue-local day
(small enough that a single window practically never reaches the cap), and
``iter_orders`` reads one window, splitting it in half by time only if it still
overflows. Windows are time-disjoint, so the orders stay unique without
deduplication.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from crude_clover.client import OFFSET_CAP, PAGE

# lineItems carry the item FK (for catalog category resolution) plus their
# modifications; payments and refunds ride alongside for flatten's refund rows.
EXPAND = "lineItems.modifications,payments,refunds"


def day_windows(date_from: str, date_to: str, tz_name: str):
    """Yield (start_ms, end_ms) per local day across an inclusive date range.

    Each window is one calendar day in ``tz_name``, from 00:00:00.000 to
    23:59:59.999 local, as UTC epoch ms — the bounds the createdTime filter
    compares against. Computed per day via the tz calendar so a DST transition
    does not shift a window.
    """
    tz = ZoneInfo(tz_name)
    day = dt.date.fromisoformat(date_from)
    last = dt.date.fromisoformat(date_to)
    while day <= last:
        start = dt.datetime.combine(day, dt.time(0, 0, 0), tzinfo=tz)
        end = dt.datetime.combine(day, dt.time(23, 59, 59, 999000), tzinfo=tz)
        yield int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        day += dt.timedelta(days=1)


class OrdersAPI:
    def __init__(self, session):
        self.session = session

    def _page(self, start_ms, end_ms, offset):
        mid = self.session.merchant_id
        params = [
            ("limit", PAGE),
            ("offset", offset),
            ("expand", EXPAND),
            ("filter", f"createdTime>={start_ms}"),
            ("filter", f"createdTime<={end_ms}"),
        ]
        body = self.session.get(f"/v3/merchants/{mid}/orders", params=params)
        return body.get("elements", [])

    def get(self, order_id, *, expand=EXPAND):
        """One order by id, expanded."""
        mid = self.session.merchant_id
        params = [("expand", expand)] if expand else None
        return self.session.get(f"/v3/merchants/{mid}/orders/{order_id}", params=params)

    def iter_modified_since(self, since_ms):
        """Yield orders with modifiedTime >= since_ms, expanded. For incremental
        syncs: pulls only what changed, not a whole date range."""
        mid = self.session.merchant_id
        yield from self.session.iter_elements(
            f"/v3/merchants/{mid}/orders",
            expand=EXPAND,
            filters=[f"modifiedTime>={since_ms}"],
        )

    def iter_orders(self, start_ms, end_ms):
        """Yield every order created in [start_ms, end_ms], expanded.

        Pages the window; if it reaches the 10000-offset cap it discards the
        partial read and splits the window in half by time, recursing into each
        disjoint half (so an arbitrarily busy window is still read in full).
        """
        buf = []
        offset = 0
        capped = False
        while True:
            elems = self._page(start_ms, end_ms, offset)
            if not elems:
                break
            buf.extend(elems)
            if len(elems) < PAGE:
                break
            offset += PAGE
            if offset >= OFFSET_CAP:
                capped = True
                break
        if not capped or end_ms - start_ms <= 1:
            yield from buf
            return
        mid_ms = (start_ms + end_ms) // 2
        yield from self.iter_orders(start_ms, mid_ms)
        yield from self.iter_orders(mid_ms + 1, end_ms)
