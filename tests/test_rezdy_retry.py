"""Unit tests for the Rezdy transport's pacing and back-off: no network, and no
test waits, because the client's clock and sleep are replaced."""

from types import SimpleNamespace

import pytest

from crude_rezdy.client import MAX_RETRIES, REQUESTS_PER_MINUTE, RezdyClient

DOWNSTREAM = ("An error happened: DownstreamCallException: [500] DOWNSTREAM_CALL_FAILURE: "
              "Downstream call failure: ERROR RESPONSE 200")


def reply(status, body=None, headers=None):
    body = body if body is not None else {"requestStatus": {"success": status < 400}, "products": []}
    return SimpleNamespace(ok=status < 400, status_code=status, json=lambda: body,
                           headers=headers or {})


def refusal(status, message):
    return reply(status, {"requestStatus": {"success": False, "error": {"errorMessage": message}}})


@pytest.fixture
def rig(monkeypatch):
    """A client answering from a script of replies, recording what it slept."""
    client = RezdyClient("KEY")
    rig = SimpleNamespace(client=client, script=[], sleeps=[], sent=[])

    def request(method, url, params=None, json=None):
        rig.sent.append(method)
        return rig.script.pop(0)

    monkeypatch.setattr(client.session, "request", request)
    monkeypatch.setattr(client, "_sleep", rig.sleeps.append)
    return rig


def test_a_429_is_retried_after_a_back_off(rig):
    rig.script = [refusal(429, "Too many requests"), reply(200)]
    assert rig.client.list_products() == []
    assert rig.sent == ["GET", "GET"]
    assert rig.sleeps == [2.0]


def test_rezdys_real_rate_limit_answer_is_retried(rig):
    # Observed against the live API: HTTP 406, errorCode 3, no Retry-After.
    limited = reply(406, {"requestStatus": {"success": False, "error": {
        "errorCode": "3", "errorMessage": "Too Many Requests (limit=100, current count=102)"}}})
    rig.script = [limited, reply(200)]
    assert rig.client.list_products() == []
    assert rig.sleeps == [2.0]


def test_a_406_that_is_not_a_rate_limit_is_raised_at_once(rig):
    rig.script = [refusal(406, "Not acceptable: bad format")]
    with pytest.raises(RuntimeError, match="bad format"):
        rig.client.list_products()
    assert rig.sleeps == []


def test_retry_after_names_the_wait(rig):
    rig.script = [reply(429, {"requestStatus": {"success": False}}, {"Retry-After": "7"}), reply(200)]
    rig.client.list_products()
    assert rig.sleeps == [7.0]


def test_the_back_off_doubles_and_gives_up_after_the_bound(rig):
    rig.script = [refusal(429, "Too many requests")] * (MAX_RETRIES + 1)
    with pytest.raises(RuntimeError) as exc:
        rig.client.list_products()
    assert len(rig.sent) == MAX_RETRIES + 1
    assert rig.sleeps == [2.0, 4.0, 8.0, 16.0, 32.0]
    assert "Too many requests" in str(exc.value)
    assert f"after {MAX_RETRIES} retries" in str(exc.value)


def test_a_downstream_failure_on_a_read_is_retried(rig):
    rig.script = [refusal(500, DOWNSTREAM), reply(200)]
    assert rig.client.list_resources() == []
    assert rig.sent == ["GET", "GET"]
    assert rig.sleeps == [2.0]


def test_a_downstream_failure_on_a_write_is_not_retried(rig):
    # The failure does not say whether the change landed; sending it again could book twice.
    rig.script = [refusal(500, DOWNSTREAM)]
    with pytest.raises(RuntimeError):
        rig.client.create_booking({"items": []})
    assert rig.sent == ["POST"]
    assert rig.sleeps == []


def test_a_429_on_a_write_is_retried(rig):
    # Rezdy refused it unread, so sending it again cannot double it.
    rig.script = [refusal(429, "Too many requests"), reply(200, {"requestStatus": {"success": True}, "booking": {}})]
    rig.client.create_booking({"items": []})
    assert rig.sent == ["POST", "POST"]


@pytest.mark.parametrize("status,message", [
    (500, "Something else broke"), (400, "bad code"), (404, "no such product"),
])
def test_other_errors_are_raised_at_once(rig, status, message):
    rig.script = [refusal(status, message)]
    with pytest.raises(RuntimeError, match=message):
        rig.client.get_product("P1")
    assert rig.sent == ["GET"]
    assert rig.sleeps == []


def test_requests_stay_under_the_minute_budget(monkeypatch):
    client = RezdyClient("KEY")
    now = [0.0]
    stamps = []

    def request(method, url, params=None, json=None):
        stamps.append(now[0])
        return reply(200)

    def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(client.session, "request", request)
    monkeypatch.setattr(client, "_clock", lambda: now[0])
    monkeypatch.setattr(client, "_sleep", sleep)
    for _ in range(3 * REQUESTS_PER_MINUTE + 10):
        client.list_products()
        now[0] += 0.01   # a request takes a moment
    assert REQUESTS_PER_MINUTE < 100
    # A minute, less the clock's last rounding.
    worst = max(sum(1 for t in stamps if start <= t < start + 59.999) for start in stamps)
    assert worst <= REQUESTS_PER_MINUTE
    assert stamps[-1] > 120   # it did wait for the minutes to pass
