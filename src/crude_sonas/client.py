"""Sonas client — DDP over a TLS websocket (no public REST API exists).

Sonas is a Meteor 2.16 app; its data rides DDP, not HTTP, so this client speaks
the protocol directly with stdlib only: a hand-rolled websocket, the DDP message
loop, and a minimongo-style document store. The wire protocol, the customised
login, the device-verification step, tenant selection, and the aldeed:tabular
fetch pattern are all documented in ``docs/sonas.md`` (the single source); this
module implements them.

Reads are DDP subscriptions (collect documents until ``ready``); writes are DDP
method calls. Auth caches the Meteor resume token in a durable side file (see
``crude_common.statestore``) and resumes from it, falling back to a full
password+fingerprint login.
"""

from __future__ import annotations

import base64
import os
import socket
import ssl
import struct
import time
import json as _json
from datetime import datetime, timedelta
from pathlib import Path

from crude_common.config import account
from crude_common.statestore import atomic_write, state_path

HOST, PORT, WS_PATH = "app.sonas.events", 443, "/websocket"
ORIGIN = "https://app.sonas.events"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# A date range wide enough to mean "all" for eventsByDateRange (1900..2100),
# expressed as Meteor EJSON dates.
EPOCH_1900_MS = -2208988800000
EPOCH_2100_MS = 4102444800000

# The device fingerprint sent as Meteor's fpIds. It is not a secret — any stable
# value works, trusted once per account via the email step (see docs/sonas.md) —
# so it has a package default and only needs a [sonas] fingerprint override to use
# a different one. A shared default is fine: device-trust is keyed per account.
DEFAULT_FINGERPRINT = "0123456789abcdef0123456789abcdef"


# ----------------------------------------------------------------------
# Websocket over a TLS socket
# ----------------------------------------------------------------------

def ws_connect(host=HOST, port=PORT, path=WS_PATH):
    raw = socket.create_connection((host, port), timeout=20)
    sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall((
        f"GET {path} HTTP/1.1\r\nHost: {host}\r\nOrigin: {ORIGIN}\r\n"
        f"User-Agent: {UA}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += sock.recv(4096)
    if b"101" not in resp.split(b"\r\n", 1)[0]:
        raise OSError(f"websocket handshake failed: {resp[:160]!r}")
    return sock


def ws_send(sock, obj):
    data = _json.dumps(obj).encode()
    n, mask = len(data), os.urandom(4)
    hdr = bytes([0x81])
    if n <= 125:      hdr += bytes([0x80 | n])
    elif n <= 0xFFFF: hdr += bytes([0xFE]) + struct.pack(">H", n)
    else:             hdr += bytes([0xFF]) + struct.pack(">Q", n)
    sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def _recvn(sock, n):
    buf = b""
    while len(buf) < n:
        c = sock.recv(n - len(buf))
        if not c:
            raise OSError("websocket connection lost")
        buf += c
    return bytes(buf)


def ws_recv(sock):
    b0, b1 = _recvn(sock, 2)
    op, n = b0 & 0x0F, b1 & 0x7F
    if n == 126:   n = struct.unpack(">H", _recvn(sock, 2))[0]
    elif n == 127: n = struct.unpack(">Q", _recvn(sock, 8))[0]
    mask = _recvn(sock, 4) if (b1 & 0x80) else None
    p = _recvn(sock, n)
    if mask:
        p = bytes(b ^ mask[i % 4] for i, b in enumerate(p))
    if op == 0x8:
        raise OSError("websocket closed by server")
    return p.decode("utf-8", "replace")


# ----------------------------------------------------------------------
# DDP over the websocket. A connection is a plain dict threaded by these
# functions (a struct, not a class — see docs/sonas.md).
# ----------------------------------------------------------------------

def ddp_connect():
    conn = {"sock": ws_connect(), "store": {}, "id": 0, "session": None, "last": None}
    ws_send(conn["sock"], {"msg": "connect", "version": "1",
                           "support": ["1", "pre2", "pre1"]})
    _pump(conn, lambda: conn["session"] is not None, 15)
    return conn


def _next_id(conn):
    conn["id"] += 1
    return str(conn["id"])


def _dispatch(conn, m):
    t = m.get("msg")
    if t == "connected":
        conn["session"] = m.get("session")
    elif t == "ping":
        ws_send(conn["sock"], {"msg": "pong", "id": m.get("id")})
    elif t == "added":
        conn["store"].setdefault(m["collection"], {})[m["id"]] = m.get("fields", {})
    elif t == "changed":
        d = conn["store"].setdefault(m["collection"], {}).setdefault(m["id"], {})
        d.update(m.get("fields", {}))
        for k in m.get("cleared", []):
            d.pop(k, None)
    elif t == "removed":
        conn["store"].get(m["collection"], {}).pop(m["id"], None)


def _pump(conn, done, timeout):
    end = time.time() + timeout
    while time.time() < end:
        try:
            conn["sock"].settimeout(max(0.5, end - time.time()))
            raw = ws_recv(conn["sock"])
        except socket.timeout:
            if done():
                return
            continue
        try:
            m = _json.loads(raw)
        except ValueError:
            continue
        _dispatch(conn, m)
        conn["last"] = m
        if done():
            return
    if not done():
        raise TimeoutError("DDP pump timed out")


def ddp_call(conn, method, params, timeout=30):
    cid = _next_id(conn)
    out = {}
    ws_send(conn["sock"], {"msg": "method", "method": method, "params": params, "id": cid})

    def got():
        m = conn["last"] or {}
        if m.get("msg") == "result" and m.get("id") == cid:
            out["value"], out["error"] = m.get("result"), m.get("error")
            return True
        return False
    _pump(conn, got, timeout)
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out.get("value")


def ddp_sub(conn, name, params, timeout=30):
    sid = _next_id(conn)
    state = {"err": None}
    ws_send(conn["sock"], {"msg": "sub", "id": sid, "name": name, "params": params})

    def done():
        m = conn["last"] or {}
        if m.get("msg") == "ready" and sid in (m.get("subs") or []):
            return True
        if m.get("msg") == "nosub" and m.get("id") == sid:
            state["err"] = m.get("error")
            return True
        return False
    _pump(conn, done, timeout)
    if state["err"]:
        raise RuntimeError(state["err"])
    return sid


def ddp_unsub(conn, sid):
    """Stop a subscription; concurrent tabular subs otherwise contaminate counts."""
    ws_send(conn["sock"], {"msg": "unsub", "id": sid})
    try:
        _pump(conn, lambda: False, 0.4)
    except TimeoutError:
        pass


def _drain(conn, secs):
    """Absorb trailing added/changed updates a publication sends after 'ready'."""
    try:
        _pump(conn, lambda: False, secs)
    except TimeoutError:
        pass


# ----------------------------------------------------------------------
# EJSON date helpers
# ----------------------------------------------------------------------

def to_ejson_date(yyyy_mm_dd: str) -> dict:
    """Encode a typed YYYY-MM-DD as an EJSON date at local midnight.

    Sonas stores an event date as venue-local midnight, so a typed date is read in
    the machine's local timezone (the venue's, for an operator on site), not UTC.
    A naive datetime's ``.timestamp()`` is interpreted as local time.
    """
    dt = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d")
    return {"$date": int(dt.timestamp() * 1000)}


def to_ejson_date_end(yyyy_mm_dd: str) -> dict:
    """EJSON exclusive upper bound for a date-range filter: local midnight of the
    day after ``yyyy_mm_dd``.

    Sonas's *ByDateRange pubs match ``from <= date < to`` on the event's start
    date, so to include every event on the to-date the upper bound must be the
    next day's local midnight, not the to-date's own midnight.
    """
    dt = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d") + timedelta(days=1)
    return {"$date": int(dt.timestamp() * 1000)}


def date_str(value) -> str:
    """Render an EJSON date ({"$date": ms}) as YYYY-MM-DD in local time.

    Rendered in the machine's local timezone (the venue's, on site) to match how
    Sonas stores venue-local dates. A UTC render shows the prior calendar day for
    any zone east of UTC — a Brisbane (+10) wedding stored as local midnight is
    the previous day in UTC. Pass other values through.
    """
    if isinstance(value, dict) and "$date" in value:
        ms = value["$date"]
        return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
    return "" if value is None else str(value)


# ----------------------------------------------------------------------
# Session token cache
# ----------------------------------------------------------------------

def token_path() -> Path:
    """Durable side file caching the Meteor resume token, namespaced by account.

    Lives under ``$XDG_STATE_HOME/crude`` (see ``crude_common.statestore``) so a
    reboot does not discard it — losing the resume token forces a fresh login,
    which from an unseen device or network re-triggers the one-time
    device-verification email (docs/sonas.md §3).
    """
    return state_path("sonas_token", account())


# Enums for human-readable output (see docs/sonas.md for the full tables).
EVENT_STATUS = {
    0: "Enquiry", 1: "Confirmed", 2: "Cancelled", 3: "DateOnHold",
    4: "Exhausted", 5: "ConfirmedPending", 6: "Completed", 7: "Idle",
}
EVENT_TYPE = {
    0: "Wedding", 1: "Blessing", 2: "Corporate", 5: "Party", 10: "RenewalOfVows",
    13: "TwilightWedding", 14: "IntimateWedding", 18: "Engagement", 23: "Elopement",
    32: "Reception", 55: "CeremonyOnly", 58: "WeddingReception",
}


class SonasClient:
    """One authenticated DDP session against Sonas, scoped to a tenant.

    Construct with the credentials, then call resource methods; the session is
    established lazily on first use (resume token, else password+fingerprint
    login, then selectTenant).
    """

    def __init__(self, user: str, digest: str, fingerprint: str = DEFAULT_FINGERPRINT,
                 tenant: str = None):
        self.user = user
        self.digest = digest
        self.fingerprint = fingerprint or DEFAULT_FINGERPRINT
        self.tenant = tenant or None
        self.conn = None
        self._token = None

    # -- session ------------------------------------------------------

    def _ensure(self):
        if self.conn is not None:
            return
        from crude_sonas.auth import sonas_login, sonas_resume
        self.conn = ddp_connect()
        cache = token_path()
        token = cache.read_text().strip() if cache.exists() else ""
        res = sonas_resume(self.conn, token) if token else None
        if not res:
            res = sonas_login(self.conn, self.user, self.digest, self.fingerprint)
            token = res.get("token") or ""
            if token:
                atomic_write(cache, token)
        self._token = token
        tenant = self.tenant or self._discover_tenant()
        ddp_call(self.conn, "selectTenant", [{"docId": tenant, "loginToken": self._token}])
        self.tenant = tenant

    def _discover_tenant(self) -> str:
        me = next(iter(self.conn["store"].get("users", {}).values()), {})
        for route in (me.get("profile") or {}).get("routes") or []:
            if route.get("tenantId"):
                return route["tenantId"]
        raise RuntimeError(
            "Could not determine the tenant id from the user record; "
            "set [sonas] tenant in config.toml.")

    def close(self):
        if self.conn is not None:
            try:
                self.conn["sock"].close()
            except Exception:
                pass
            self.conn = None

    # -- reads --------------------------------------------------------

    def read_pub(self, name: str, params: list, collection: str = None,
                 drain: float = 2.0) -> list:
        """Subscribe to a publication, collect its documents, unsubscribe.

        ``collection`` names the store collection to return; None auto-detects:
        documents that appeared in any collection during the subscription are
        returned with their collection name under ``_collection`` (the discovery
        tool for pubs whose collection is unknown). Docs carry the document id
        under ``_id``. The final unsubscribe matters: Meteor dedupes identical
        name+params subscriptions per connection, so without it a read-after-write
        would return a stale empty set.
        """
        self._ensure()
        store = self.conn["store"]
        if collection:
            store.pop(collection, None)
            before = {}
        else:
            before = {c: set(docs) for c, docs in store.items()}
        sid = ddp_sub(self.conn, name, params, timeout=30)
        _drain(self.conn, drain)
        if collection:
            docs = [dict(doc, _id=i) for i, doc in store.get(collection, {}).items()]
        else:
            docs = []
            for c, d in store.items():
                for i in set(d) - before.get(c, set()):
                    docs.append(dict(d[i], _id=i, _collection=c))
        ddp_unsub(self.conn, sid)
        return docs

    def read_tabular(self, table: str, *, data_pub: str, selector: dict = None,
                     sort: list = None, skip: int = 0, limit: int = 50,
                     search: str = "", proj: dict = None, collection: str = None):
        """aldeed:tabular two-step (docs/sonas.md §5): ``tabular_getInfo``
        publishes ids and counts into ``tabular_records``; the table's data pub
        delivers the documents. Data-pub signatures vary per table, so the known
        arg orders are tried until documents arrive. Returns ``(rows, info)``
        with ``info = {recordsTotal, recordsFiltered}``.
        """
        self._ensure()
        store = self.conn["store"]
        store.pop("tabular_records", None)
        sid = ddp_sub(self.conn, "tabular_getInfo",
                      [table, selector or {}, sort or [], skip, limit, search])
        _drain(self.conn, 1)
        rec = next(iter(store.get("tabular_records", {}).values()), {})
        ids = rec.get("ids") or []
        info = {"recordsTotal": rec.get("recordsTotal"),
                "recordsFiltered": rec.get("recordsFiltered")}
        rows = []
        if ids:
            proj_obj = proj or {}
            for params in ([table, ids, proj_obj], [ids, proj_obj],
                           [table, ids], [ids]):
                try:
                    rows = self.read_pub(data_pub, params, collection=collection)
                except RuntimeError:
                    continue
                if rows:
                    break
        ddp_unsub(self.conn, sid)
        return rows, info

    def list_events(self, from_: str = None, to: str = None) -> list:
        """Events whose date falls in [from, to] (default all time). Returns docs
        with the document id merged in under ``_id``."""
        frm = to_ejson_date(from_) if from_ else {"$date": EPOCH_1900_MS}
        to_d = to_ejson_date_end(to) if to else {"$date": EPOCH_2100_MS}
        return self.read_pub("eventsByDateRange", [frm, to_d], collection="events")

    def get_event(self, event_id: str) -> dict:
        for ev in self.list_events():
            if ev["_id"] == event_id:
                return ev
        raise RuntimeError(f"event {event_id} not found")

    # -- method calls (writes, and the few read-methods) ----------------

    def call(self, method: str, *args):
        """Invoke a DDP method; ``args`` are the DDP params array (typically one
        object argument)."""
        self._ensure()
        return ddp_call(self.conn, method, list(args))
