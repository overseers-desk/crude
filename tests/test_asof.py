"""crude_common.asof, the WORLD_AS_OF core: the three semantics, the parse,
the clamps, the post-filter, and the shared write gate. No network anywhere.

Semantic 1 (unset) is asserted as zero behavioural change: every helper passes
its input through untouched. Semantic 2 (set) is the honest boundary: drop
created-after, flag mutated-after, clamp server bounds, refuse writes.
Semantic 3 (set but unparseable, including timezone-naive) is a hard failure.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import typer
from typer.testing import CliRunner

from crude_common import asof

BOUND = "2026-07-12T17:07:00+10:00"
BOUND_UTC = datetime(2026, 7, 12, 7, 7, 0, tzinfo=timezone.utc)

BEFORE = "2026-07-01T00:00:00Z"
AFTER = "2026-07-13T00:00:00Z"


@pytest.fixture
def bound(monkeypatch):
    monkeypatch.setenv(asof.ENV, BOUND)


@pytest.fixture
def unbound(monkeypatch):
    monkeypatch.delenv(asof.ENV, raising=False)


# ----------------------------------------------------------------------
# The three semantics
# ----------------------------------------------------------------------


def test_unset_is_none_and_inactive(unbound):
    assert asof.world_as_of() is None
    assert not asof.active()
    assert asof.check_env() is None


def test_set_parses_to_aware_instant(bound):
    assert asof.world_as_of() == BOUND_UTC
    assert asof.active()
    assert asof.bound_ms() == int(BOUND_UTC.timestamp() * 1000)


@pytest.mark.parametrize("bad", [
    "yesterday",
    "2026-13-45T99:00:00+10:00",
    "2026-07-12T17:07:00",      # timezone-naive: ambiguous, refused
    "2026-07-12",               # date only, no offset
])
def test_unparseable_or_naive_is_a_hard_failure(monkeypatch, bad):
    monkeypatch.setenv(asof.ENV, bad)
    with pytest.raises(asof.WorldAsOfError):
        asof.world_as_of()


def test_check_env_aborts_the_process_on_a_bad_value(monkeypatch):
    monkeypatch.setenv(asof.ENV, "not-a-time")
    with pytest.raises(typer.Exit) as exc:
        asof.check_env()
    assert exc.value.exit_code == 2


def test_z_suffix_and_colonless_offset_parse(monkeypatch):
    monkeypatch.setenv(asof.ENV, "2026-07-12T07:07:00Z")
    assert asof.world_as_of() == BOUND_UTC
    monkeypatch.setenv(asof.ENV, "2026-07-12T17:07:00+1000")
    assert asof.world_as_of() == BOUND_UTC


def test_launcher_gate_fails_before_anything_else(monkeypatch):
    # End to end through the CLI: a bad value aborts with a clear message and
    # exit 2, before the command body (or any request) could run.
    from crude_common import launcher

    monkeypatch.setattr(launcher, "refresh", lambda: None)
    result = CliRunner().invoke(launcher.app, [], env={asof.ENV: "garbage"})
    assert result.exit_code == 2
    assert "WORLD_AS_OF" in result.output


# ----------------------------------------------------------------------
# Record-stamp parsing (the per-backend wire formats)
# ----------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("2026-07-01T00:00:00Z", datetime(2026, 7, 1, tzinfo=timezone.utc)),
    ("2026-07-01T10:00:00+10:00", datetime(2026, 7, 1, tzinfo=timezone.utc)),
    ("2026-07-01T00:00:00+0000", datetime(2026, 7, 1, tzinfo=timezone.utc)),  # Facebook
    ("2026-07-01 00:00:00", datetime(2026, 7, 1, tzinfo=timezone.utc)),       # Odoo, naive=UTC
    ("2026-07-01", datetime(2026, 7, 1, tzinfo=timezone.utc)),
    ({"$date": 1782864000000}, datetime(2026, 7, 1, tzinfo=timezone.utc)),    # Sonas EJSON
    (1782864000000, datetime(2026, 7, 1, tzinfo=timezone.utc)),               # Clover epoch ms
    (1782864000, datetime(2026, 7, 1, tzinfo=timezone.utc)),                  # epoch seconds
    ("/Date(1782864000000+0000)/", datetime(2026, 7, 1, tzinfo=timezone.utc)),  # Xero .NET
    ("/Date(1782864000000)/", datetime(2026, 7, 1, tzinfo=timezone.utc)),
])
def test_parse_stamp_wire_formats(value, expected):
    assert asof.parse_stamp(value) == expected


@pytest.mark.parametrize("value", [None, "", "not a date", {}, {"$date": None}, True, []])
def test_parse_stamp_never_guesses(value):
    assert asof.parse_stamp(value) is None


# ----------------------------------------------------------------------
# Server-side clamps and window checks
# ----------------------------------------------------------------------


def test_clamp_upper_passes_through_when_unbound(unbound):
    assert asof.clamp_upper_iso(None) is None
    assert asof.clamp_upper_iso(AFTER) == AFTER
    assert asof.clamp_upper_ms(None) is None
    assert asof.clamp_upper_ms(99) == 99


def test_clamp_upper_iso_under_bound(bound):
    assert asof.clamp_upper_iso(None) == "2026-07-12T07:07:00Z"     # bound injected
    assert asof.clamp_upper_iso(BEFORE) == BEFORE                    # earlier user value kept verbatim
    assert asof.clamp_upper_iso(AFTER) == "2026-07-12T07:07:00Z"     # later value clamped
    assert asof.clamp_upper_iso("gibberish") == "2026-07-12T07:07:00Z"  # unparseable clamped, not trusted


def test_clamp_upper_ms_under_bound(bound):
    b_ms = asof.bound_ms()
    assert asof.clamp_upper_ms(None) == b_ms
    assert asof.clamp_upper_ms(b_ms - 5) == b_ms - 5
    assert asof.clamp_upper_ms(b_ms + 5) == b_ms


def test_window_starting_after_cutoff_refuses(bound):
    with pytest.raises(typer.Exit) as exc:
        asof.check_window_start(AFTER)
    assert exc.value.exit_code == 1
    asof.check_window_start(BEFORE)  # a window inside the world passes


def test_window_check_is_a_noop_when_unbound(unbound):
    asof.check_window_start(AFTER)


# ----------------------------------------------------------------------
# Post-filter: drop created-after, flag mutated-after
# ----------------------------------------------------------------------

ROWS = [
    {"id": 1, "created": BEFORE, "updated": BEFORE},
    {"id": 2, "created": BEFORE, "updated": AFTER},   # pre-cutoff record edited later
    {"id": 3, "created": AFTER, "updated": AFTER},    # did not exist at the cutoff
    {"id": 4},                                        # no stamps: kept, never guessed at
]


def test_post_filter_unbound_is_identity(unbound):
    kept, dropped, mutated = asof.post_filter(ROWS, "created", "updated")
    assert kept is ROWS and dropped == 0 and mutated == 0


def test_post_filter_drops_and_flags(bound):
    kept, dropped, mutated = asof.post_filter(ROWS, "created", "updated")
    assert [r["id"] for r in kept] == [1, 2, 4]
    assert dropped == 1 and mutated == 1
    assert asof.MARKER_KEY not in kept[0]
    assert kept[1][asof.MARKER_KEY] == asof.MUTATED
    assert asof.MARKER_KEY not in ROWS[1]  # inputs are not mutated


def test_post_filter_dotted_path_and_callable_keys(bound):
    rows = [{"meta": {"created": AFTER}}, {"meta": {"created": BEFORE}}]
    kept, dropped, _ = asof.post_filter(rows, "meta.created")
    assert dropped == 1 and kept == [{"meta": {"created": BEFORE}}]
    kept, dropped, _ = asof.post_filter(rows, lambda r: r["meta"]["created"])
    assert dropped == 1


def test_bound_records_emits_the_notice(bound, capsys):
    kept = asof.bound_records(ROWS, "created", "updated", what="thing")
    assert len(kept) == 3
    err = capsys.readouterr().err
    assert "WORLD_AS_OF" in err and "1 thing(s) created after cutoff dropped" in err
    assert "1 mutated after cutoff" in err


def test_bound_records_is_silent_when_unbound(unbound, capsys):
    assert asof.bound_records(ROWS, "created") is ROWS
    assert capsys.readouterr().err == ""


def test_check_record_refuses_created_after(bound):
    with pytest.raises(typer.Exit) as exc:
        asof.check_record({"created": AFTER}, "created", what="widget")
    assert exc.value.exit_code == 1


def test_check_record_flags_mutated_after(bound):
    rec = asof.check_record({"created": BEFORE, "updated": AFTER}, "created", "updated")
    assert rec[asof.MARKER_KEY] == asof.MUTATED


def test_check_record_unbound_is_identity(unbound):
    rec = {"created": AFTER}
    assert asof.check_record(rec, "created") is rec


def test_flag_current_state(bound):
    assert asof.flag_current_state({"a": 1})[asof.MARKER_KEY] == asof.CURRENT_STATE


def test_flag_current_state_unbound_is_identity(unbound):
    rec = {"a": 1}
    assert asof.flag_current_state(rec) is rec


# ----------------------------------------------------------------------
# Write refusal: the shared gate both layers use
# ----------------------------------------------------------------------


def test_do_write_refuses_under_bound(bound, capsys):
    from crude_common.writeio import do_write

    ran = {"n": 0}

    def action():
        ran["n"] += 1

    with pytest.raises(typer.Exit) as exc:
        do_write(action, "create thing", yes=True)
    assert exc.value.exit_code == 1
    assert ran["n"] == 0  # the action never ran; nothing touched the backend
    assert "WORLD_AS_OF" in capsys.readouterr().err


def test_do_write_passes_through_when_unbound(unbound, capsys):
    from crude_common.writeio import do_write

    ran = {"n": 0}
    do_write(lambda: ran.update(n=1), "create thing", yes=True)
    assert ran["n"] == 1
    assert "done" in capsys.readouterr().out


def test_guard_write_raises_under_bound(bound):
    with pytest.raises(asof.WorldAsOfError) as exc:
        asof.guard_write("mutate the world")
    assert "mutate the world" in str(exc.value)


def test_guard_write_is_a_noop_when_unbound(unbound):
    asof.guard_write("anything")
