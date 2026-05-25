"""Skål Australia session-cookie login."""

import re
import requests

LOGIN_URL = "https://australia.skal.org/web/login"


def skal_login(username: str, password: str) -> str:
    """Perform the 2-step form login and return the session_id cookie value."""
    s = requests.Session()
    r = s.get(LOGIN_URL)
    m = re.search(r'name=["\']csrf_token["\'] value=["\']([^"\']+)["\']', r.text)
    if not m:
        raise RuntimeError("Could not extract CSRF token from login page.")
    csrf = m.group(1)
    s.post(
        LOGIN_URL,
        data={"login": username, "password": password, "csrf_token": csrf, "redirect": ""},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        allow_redirects=True,
    )
    session_id = s.cookies.get("session_id")
    if not session_id:
        raise RuntimeError("Login failed: no session_id cookie returned. Check credentials.")
    return session_id
