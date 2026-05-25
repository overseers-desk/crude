"""ATDW OAuth2 implicit grant login and token caching."""

import re
import requests

OAUTH_LOGIN_URL = "https://oauth.atdw-online.com.au/login"
OAUTH_AUTHORIZE_URL = "https://oauth.atdw-online.com.au/oauth2/authorize"
CLIENT_ID = "12349d7eb9c04d6c8613e4b5f97854f3"
REDIRECT_URI = "https://www.atdw-online.com.au"


def atdw_login(username: str, password: str) -> str:
    """Perform the 3-step OAuth2 implicit grant login and return the JWT bearer token."""
    s = requests.Session()

    # Step 1: POST credentials to the OAuth login form
    s.post(
        OAUTH_LOGIN_URL,
        data={"username": username, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        allow_redirects=False,
    )

    # Step 2: Follow OAuth2 implicit grant authorize endpoint
    r = s.get(
        OAUTH_AUTHORIZE_URL,
        params={
            "response_type": "token",
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "state": "%2Fhome",
        },
        allow_redirects=False,
    )

    # Step 3: Extract JWT from the redirect URL fragment
    location = r.headers.get("location", "")
    match = re.search(r"access_token=([^&]+)", location)
    if not match:
        raise RuntimeError(
            f"Login failed: no access_token in redirect location. "
            f"HTTP {r.status_code}. Location: {location!r}"
        )
    return match.group(1)
