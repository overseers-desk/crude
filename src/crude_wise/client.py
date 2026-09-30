"""Wise transport: a personal-token bearer session over the CalVer REST API.

One WiseSession carries the personal API token, issued once in the Wise web UI
(a UUID today; a JWT once Wise's token migration lands) and sent as an opaque
bearer string, never parsed. Wise versions its API by calendar quarter in the URL
path (``/2026Q3/...``), one version for every endpoint, so the version is a single
prefix on the base URL. The profile every profile-scoped read is made for is
resolved lazily from ``GET /profiles``: the config's ``profile_id`` when set, else
the account's one business profile.

The balance statement, which is the transaction ledger, is SCA-protected for
profiles registered outside US, AU, NZ, SG, CA and MY. The first call answers 403
with a one-time token in ``x-2fa-approval``; the same request succeeds when resent
with that token and its RSA-SHA256 signature in ``X-Signature``. The key is the
account's own: the public half is uploaded on the Wise API tokens page, the
private half is a PEM file named by ``private_key`` in config. Signing shells out
to openssl, so the key never enters this process.

Three list shapes: transfers and profiles answer with a bare array; recipients
(``/accounts``) wrap a page as ``{"content": [...], "seekPositionForNext": n}``;
activities as ``{"activities": [...], "cursor": "..."}``.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

from crude_common import asof
from crude_common.httpapi import HttpSession

BASE = "https://api.wise.com"
SANDBOX_BASE = "https://api.wise-sandbox.com"
API_VERSION = "2026Q3"

# Per-page sizes. Recipients cap at 20 per page; activities at 100; transfers
# document no cap, and 100 keeps a year of payouts to a few requests.
RECIPIENT_PAGE = 20
ACTIVITY_PAGE = 100
TRANSFER_PAGE = 100

SCA_SETUP = (
    "Wise requires strong customer authentication for this read (the profile is "
    "registered outside US/AU/NZ/SG/CA/MY). Generate a keypair "
    "(openssl genrsa -out wise-sca.pem 2048; openssl rsa -in wise-sca.pem -pubout "
    "-out wise-sca.pub), upload wise-sca.pub on the Wise API tokens page (Manage "
    "public keys), and set private_key to the .pem path under {which}."
)


class WiseError(RuntimeError):
    """A Wise API error, carrying the HTTP status, Wise's error code, and, for a
    403 that asks for strong customer authentication, the one-time token."""

    def __init__(self, message, *, status=None, code=None, ott=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.ott = ott
        self.message = message


def base_url(environment) -> str:
    """The production host by default; the sandbox host for a sandbox environment."""
    if environment and str(environment).strip().lower() in ("sandbox", "demo"):
        return SANDBOX_BASE
    return BASE


def sign_ott(ott: str, key_path) -> str:
    """The base64 RSA-SHA256 (PKCS#1 v1.5) signature of the one-time token's ASCII
    bytes, made by openssl with the private key at ``key_path``."""
    path = Path(key_path).expanduser()
    if not path.exists():
        raise WiseError(f"private_key {path} not found")
    try:
        done = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(path)],
            input=ott.encode("ascii"), capture_output=True, check=True,
        )
    except FileNotFoundError:
        raise WiseError("openssl is not on PATH; it signs the SCA one-time token") from None
    except subprocess.CalledProcessError as e:
        detail = e.stderr.decode(errors="replace").strip()
        raise WiseError(f"openssl could not sign with {path}: {detail}") from None
    return base64.b64encode(done.stdout).decode("ascii")


def select_profile(profiles: list, which: str) -> dict:
    """The profile to act on when config names none: the one business profile.

    A personal token belongs to a business account, which carries its business
    profile beside the holder's personal one. Several business profiles need
    ``profile_id`` in config; the error lists them.
    """
    business = [p for p in profiles if p.get("type") == "BUSINESS"]
    if len(business) == 1:
        return business[0]
    if not business and len(profiles) == 1:
        return profiles[0]
    if not business:
        raise WiseError(f"the token reaches no business profile; set profile_id under {which}")
    listing = ", ".join(f"{p.get('id')} ({p.get('businessName') or p.get('fullName')})" for p in business)
    raise WiseError(f"the token reaches several business profiles: {listing}; set profile_id under {which}")


def _error_body(r) -> dict:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class WiseSession(HttpSession):
    def __init__(self, token, *, base=BASE, profile_id=None, private_key=None, account=None):
        super().__init__(f"{base}/{API_VERSION}", timeout=60)
        self.account = account
        self.private_key = private_key
        self._profile_id = int(profile_id) if profile_id else None
        self._profiles = None
        self.session.headers.update(
            {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        )

    @property
    def which(self) -> str:
        """The config section label for messages: [wise] or [wise.<account>]."""
        return f"[wise.{self.account}]" if self.account else "[wise]"

    # ------------------------------------------------------------------
    # Transport: errors, and the SCA sign-and-retry
    # ------------------------------------------------------------------

    def _raise(self, r) -> None:
        if r.status_code == 401:
            raise WiseError(
                "Wise rejected the token (HTTP 401): it was revoked, mistyped, or "
                f"issued for another environment. Check api_token under {self.which}.",
                status=401,
            )
        ott = r.headers.get("x-2fa-approval")
        if r.status_code == 403 and r.headers.get("x-2fa-approval-result", "").upper() == "REJECTED":
            if ott:
                raise WiseError("strong customer authentication required", status=403, ott=ott)
            # REJECTED with no token is the answer to a signed retry Wise could
            # not verify.
            raise WiseError(
                "Wise rejected the signed SCA challenge (HTTP 403): check that the "
                f"public key matching private_key ({self.private_key}) is uploaded on "
                "the account's API tokens page.",
                status=403,
            )
        body = _error_body(r)
        errors = body.get("errors") or []
        first = errors[0] if errors and isinstance(errors[0], dict) else {}
        message = (body.get("message") or first.get("message") or body.get("error")
                   or f"HTTP {r.status_code}: {r.text[:200]}")
        raise WiseError(message, status=r.status_code, code=body.get("code") or first.get("code"))

    def _request(self, method, path, *, headers=None, **kw):
        """The shared request path, plus one signed retry when Wise answers a
        403 carrying a one-time token."""
        try:
            return super()._request(method, path, headers=headers, **kw)
        except WiseError as e:
            if e.ott is None:
                raise
            if "x-2fa-approval" in (headers or {}):
                raise WiseError(
                    "Wise rejected the signed SCA challenge (HTTP 403): check that the "
                    f"public key matching private_key ({self.private_key}) is uploaded on "
                    "the account's API tokens page.",
                    status=403,
                ) from None
            if not self.private_key:
                raise WiseError(SCA_SETUP.format(which=self.which), status=403) from None
            signed = dict(headers or {})
            signed["x-2fa-approval"] = e.ott
            signed["X-Signature"] = sign_ott(e.ott, self.private_key)
            return self._request(method, path, headers=signed, **kw)

    def get(self, path, *, params=None):
        return self._get(path, params=params)

    # ------------------------------------------------------------------
    # Profiles and the selected profile
    # ------------------------------------------------------------------

    def profiles(self) -> list:
        """The token's profiles, fetched once per process."""
        if self._profiles is None:
            data = self.get("/profiles")
            self._profiles = data if isinstance(data, list) else []
        return self._profiles

    @property
    def profile_id(self) -> int:
        """The profile every profile-scoped read is made for (see select_profile)."""
        if self._profile_id is None:
            self._profile_id = int(select_profile(self.profiles(), self.which)["id"])
        return self._profile_id

    def profile(self, profile_id=None) -> dict:
        pid = int(profile_id) if profile_id else self.profile_id
        for p in self.profiles():
            if p.get("id") == pid:
                return p
        data = self.get(f"/profiles/{pid}")
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # Balances and the statement
    # ------------------------------------------------------------------

    def balances(self) -> list:
        """Every STANDARD and SAVINGS balance of the selected profile."""
        data = self.get(f"/profiles/{self.profile_id}/balances",
                        params={"types": "STANDARD,SAVINGS"})
        return data if isinstance(data, list) else []

    def statement(self, balance_id, currency, *, start, end, kind="COMPACT") -> dict:
        """The balance statement over [start, end] (ISO-8601 UTC instants), at most
        469 days. SCA-signed on the retry when the profile's region requires it."""
        return self.get(
            f"/profiles/{self.profile_id}/balance-statements/{balance_id}/statement.json",
            params={"currency": currency, "intervalStart": start, "intervalEnd": end,
                    "type": kind},
        )

    # ------------------------------------------------------------------
    # Paged collections: transfers (offset), recipients (seek), activities (cursor)
    # ------------------------------------------------------------------

    def iter_transfers(self, *, params=None, max_items=None):
        p = dict(params or {})
        p["profile"] = self.profile_id
        offset, got = 0, 0
        while True:
            p.update({"limit": TRANSFER_PAGE, "offset": offset})
            page = self.get("/transfers", params=p)
            page = page if isinstance(page, list) else []
            for item in page:
                yield item
                got += 1
                if max_items and got >= max_items:
                    return
            if len(page) < TRANSFER_PAGE:
                return
            offset += len(page)

    def iter_recipients(self, *, params=None, max_items=None):
        p = dict(params or {})
        p.update({"profileId": self.profile_id, "size": RECIPIENT_PAGE})
        got = 0
        while True:
            payload = self.get("/accounts", params=p)
            page = payload.get("content") or [] if isinstance(payload, dict) else []
            for item in page:
                yield item
                got += 1
                if max_items and got >= max_items:
                    return
            seek = payload.get("seekPositionForNext") if isinstance(payload, dict) else None
            if not page or seek is None:
                return
            p["seekPosition"] = seek

    def iter_activities(self, *, params=None, max_items=None):
        p = dict(params or {})
        p["size"] = ACTIVITY_PAGE
        got = 0
        while True:
            payload = self.get(f"/profiles/{self.profile_id}/activities", params=p)
            page = payload.get("activities") or [] if isinstance(payload, dict) else []
            for item in page:
                yield item
                got += 1
                if max_items and got >= max_items:
                    return
            cursor = payload.get("cursor") if isinstance(payload, dict) else None
            if not page or not cursor:
                return
            p["nextCursor"] = cursor

    def collect(self, walker, *, params=None, limit=None, created=None, modified=None,
                what="record") -> list:
        """A bounded list read over one of the ``iter_*`` methods above.

        Their server-side date filters are clamped by the caller under
        WORLD_AS_OF; this applies the exact post-filter on top and, under a bound,
        walks the whole set before trimming to `limit` so rows dropped for being
        too new do not eat into what the caller asked for.
        """
        if asof.world_as_of() is None:
            return list(walker(params=params, max_items=limit))
        items = list(walker(params=params))
        kept = asof.bound_records(items, created, modified, what=what)
        return kept[:limit] if limit else kept
