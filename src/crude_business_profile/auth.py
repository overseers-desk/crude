"""The Google token file for crude-business-profile, and the one-time consent that makes it.

The durable credential is a refresh token of a Google account that manages the
business, scope ``business.manage``. Google does not rotate it on use, so the
side file under the XDG state dir is written by ``auth`` alone and only read
afterwards; the hour-long access token lives in memory. The file's
shape is the authorised-user JSON google-auth writes and reads (``token``,
``refresh_token``, ``token_uri``, ``client_id``, ``client_secret``, ``scopes``,
``expiry``), so a token made elsewhere with that library copies into place
unchanged, and one made here serves a program using that library.

The consent is Google's installed-app flow: a Desktop-app OAuth client (its id
and secret in config; Google treats the secret of an installed app as not
secret), a loopback redirect on localhost, ``access_type=offline`` for the
refresh token and ``prompt=consent`` so one is issued even when the account has
consented before. The browser half is crude_common.oauth_consent.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

from crude_business_profile.client import SCOPE, TOKEN_URL, BusinessProfileError
from crude_common.oauth_consent import generate_state, loopback_code, manual_code
from crude_common.statestore import state_path, write_or_exit

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def token_store_path(account) -> Path:
    """The side file: ``business_profile_token.json`` under ``$XDG_STATE_HOME/crude``,
    suffixed with the account name for a named account."""
    return state_path("business_profile_token.json", account)


def load_token(account):
    """The authorised-user record from the side file, or None when there is none
    (or it is not JSON, which reads as none: the cure is the same consent)."""
    path = token_store_path(account)
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and info.get("refresh_token") else None


def save_token(account, info: dict) -> None:
    write_or_exit(token_store_path(account), json.dumps(info, indent=2))


def build_authorize_url(client_id, redirect_uri, state) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(client_id, client_secret, code, redirect_uri) -> dict:
    """Exchange the consent's code for a token set at Google's token endpoint."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }
    r = requests.post(TOKEN_URL, data=data, headers={"Accept": "application/json"}, timeout=30)
    if not r.ok:
        try:
            body = r.json()
        except ValueError:
            body = {}
        detail = body.get("error_description") or body.get("error") or r.text
        raise BusinessProfileError(
            f"Google token request failed: HTTP {r.status_code} {detail}", status=r.status_code)
    grant = r.json()
    if not grant.get("refresh_token"):
        raise BusinessProfileError(
            "Google issued no refresh token; the consent cannot serve unattended runs")
    return grant


def authorized_user_info(client_id, client_secret, grant: dict) -> dict:
    """The side file's record from a fresh grant, in google-auth's authorised-user shape."""
    expiry = datetime.fromtimestamp(time.time() + int(grant.get("expires_in", 3600)), timezone.utc)
    return {
        "token": grant.get("access_token"),
        "refresh_token": grant["refresh_token"],
        "token_uri": TOKEN_URL,
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": [SCOPE],
        "universe_domain": "googleapis.com",
        "account": "",
        "expiry": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def loopback_authorize(client_id, client_secret, redirect_uri, *, open_browser=True,
                       timeout=300) -> dict:
    """Browser consent on a loopback port; returns the record to save."""
    state = generate_state()
    url = build_authorize_url(client_id, redirect_uri, state)
    code = loopback_code(url, redirect_uri, state, error=BusinessProfileError,
                         open_browser=open_browser, timeout=timeout,
                         title="crude-business-profile")
    return authorized_user_info(client_id, client_secret,
                                exchange_code(client_id, client_secret, code, redirect_uri))


def manual_authorize(client_id, client_secret, redirect_uri) -> dict:
    """Paste-based consent for a headless box; returns the record to save."""
    state = generate_state()
    url = build_authorize_url(client_id, redirect_uri, state)
    code = manual_code(url, state, error=BusinessProfileError)
    return authorized_user_info(client_id, client_secret,
                                exchange_code(client_id, client_secret, code, redirect_uri))
