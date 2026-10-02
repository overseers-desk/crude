"""crude_mautic tests: response shapes, paging, and the submission counting surface.

No network. The Mautic API answers in shapes that differ per entity and carries
answers as they were stored, so the parts worth pinning are the normalisation
(rows, list_key, unescape_results), the start/limit walk, and the filtering and
grouping that `form submissions` puts on top.
"""

import pytest
from typer.testing import CliRunner

from crude_mautic import cli, cli_resources
from crude_mautic.client import MauticSession, list_key, rows, unescape_results

runner = CliRunner()


def _cmds(typer_app):
    return {c.name for c in typer_app.registered_commands}


def _groups(typer_app):
    return {g.name for g in typer_app.registered_groups}


# ----------------------------------------------------------------------
# Response shapes
# ----------------------------------------------------------------------

def test_rows_reads_both_shapes():
    """Forms answer with an array; every other entity with an object keyed by id."""
    assert rows({"forms": [{"id": 1}]}, "forms") == [{"id": 1}]
    assert rows({"contacts": {"9": {"id": 9}}}, "contacts") == [{"id": 9}]
    assert rows({"total": 0, "forms": []}, "forms") == []
    assert rows({}, "forms") == []


def test_segments_answer_under_their_older_name():
    assert list_key("segments") == "lists"
    assert list_key("contacts") == "contacts"
    assert rows({"lists": {"1": {"id": 1}}}, "segments") == [{"id": 1}]


def test_unescape_results_folds_one_answer_stored_two_ways():
    a = unescape_results({"results": {"topic": "NDIS &amp; Disability"}})
    b = unescape_results({"results": {"topic": "NDIS & Disability"}})
    assert a["results"]["topic"] == b["results"]["topic"] == "NDIS & Disability"


def test_unescape_results_leaves_non_string_answers_alone():
    rec = unescape_results({"id": 4, "results": {"n": 3, "topic": "a &lt; b"}})
    assert rec["results"] == {"n": 3, "topic": "a < b"}
    assert rec["id"] == 4


def test_unescape_results_passes_through_a_record_without_answers():
    assert unescape_results({"id": 4}) == {"id": 4}


# ----------------------------------------------------------------------
# Paging
# ----------------------------------------------------------------------

class _FakeSession(MauticSession):
    """A session whose GET replays canned pages, recording the params it was given."""

    def __init__(self, pages):
        super().__init__("https://mautic.example.com", "u", "p")
        self._pages = list(pages)
        self.calls = []

    def get(self, path, *, params=None):
        self.calls.append((path, dict(params or {})))
        return self._pages.pop(0)


def test_iter_rows_walks_until_the_reported_total():
    page = {"total": 150, "contacts": {str(i): {"id": i} for i in range(100)}}
    tail = {"total": 150, "contacts": {str(i): {"id": i} for i in range(100, 150)}}
    sess = _FakeSession([page, tail])
    assert len(list(sess.iter_rows("/contacts", "contacts"))) == 150
    assert [c[1]["start"] for c in sess.calls] == [0, 100]


def test_iter_rows_stops_at_max_items_without_a_second_call():
    sess = _FakeSession([{"total": 7820, "contacts": {str(i): {"id": i} for i in range(100)}}])
    assert len(list(sess.iter_rows("/contacts", "contacts", max_items=3))) == 3
    assert len(sess.calls) == 1


def test_iter_rows_stops_on_a_short_page_when_no_total_is_given():
    sess = _FakeSession([{"forms": [{"id": 1}]}])
    assert list(sess.iter_rows("/forms", "forms")) == [{"id": 1}]
    assert len(sess.calls) == 1


def test_base_url_takes_the_api_suffix_once():
    sess = MauticSession("https://mautic.example.com/", "u", "p")
    assert sess.base_url == "https://mautic.example.com/api"
    assert sess.instance_url == "https://mautic.example.com"


# ----------------------------------------------------------------------
# The counting surface
# ----------------------------------------------------------------------

FORM = {"id": 5, "alias": "website_en", "name": "Website Enquiry",
        "dateAdded": "2026-07-16T22:12:49+00:00", "fields": {}}

SUBMISSIONS = [
    {"id": 1, "dateSubmitted": "2026-08-01T00:00:00+00:00",
     "results": {"email": "a@example.com", "topic": "Celebrate"}},
    {"id": 2, "dateSubmitted": "2026-08-02T00:00:00+00:00",
     "results": {"email": "b@example.com", "topic": "NDIS &amp; Disability"}},
    {"id": 3, "dateSubmitted": "2026-08-03T00:00:00+00:00",
     "results": {"email": "c@example.com", "topic": "NDIS & Disability"}},
    {"id": 4, "dateSubmitted": "2026-08-04T00:00:00+00:00",
     "results": {"email": "d@example.com", "topic": "Stallholder EOI"}},
]


class _StubSession:
    """Answers the two calls the submissions command makes."""

    instance_url = "https://mautic.example.com"

    def get(self, path, *, params=None):
        if path == "/forms":
            return {"total": 1, "forms": [FORM]}
        raise AssertionError(f"unexpected GET {path}")

    def one(self, path, entity_singular):
        return FORM

    def fetch(self, path, entity, **kw):
        assert path == "/forms/5/submissions"
        return list(SUBMISSIONS)


@pytest.fixture
def stubbed(monkeypatch):
    sess = _StubSession()
    monkeypatch.setattr(cli_resources, "_session", lambda: sess)
    # The Submitted column asks the config on disk for its timezone.
    monkeypatch.setattr("crude_common.config.find_config", lambda: "config.toml")
    monkeypatch.setattr("crude_common.config.read_config", lambda _p: {})
    return sess


def _run(*argv):
    result = runner.invoke(cli.app, list(argv))
    assert result.exit_code == 0, result.output
    return result.output


def test_where_narrows_a_shared_form_to_one_pages_slice(stubbed):
    out = _run("form", "submissions", "website_en", "--where", "topic=Stallholder EOI")
    assert "d@example.com" in out
    assert "a@example.com" not in out
    assert "1 submission(s) found." in out


def test_group_by_counts_every_slice_and_folds_the_escaped_answer(stubbed):
    out = _run("form", "submissions", "website_en", "--group-by", "topic")
    # The two spellings of the NDIS answer count as one value with two submissions.
    assert "NDIS & Disability" in out
    assert "&amp;" not in out
    assert "3 distinct topic(s) found." in out
    assert "4 submission(s) across them." in out


def test_where_clauses_accumulate(stubbed):
    out = _run("form", "submissions", "website_en",
               "--where", "topic=Celebrate", "--where", "email=nobody@example.com")
    assert "0 submission(s) found." in out


def test_where_without_an_equals_sign_is_rejected(stubbed):
    result = runner.invoke(cli.app, ["form", "submissions", "website_en", "--where", "topic"])
    assert result.exit_code != 0
    assert "FIELD=VALUE" in result.output


def test_a_form_is_addressable_by_alias_or_id(stubbed):
    assert cli_resources._resolve_form(stubbed, "website_en")["id"] == 5
    assert cli_resources._resolve_form(stubbed, "5")["id"] == 5


def test_an_unknown_alias_names_the_ones_that_exist(stubbed):
    result = runner.invoke(cli.app, ["form", "submissions", "nosuchform"])
    assert result.exit_code == 1
    assert "website_en" in result.output


def test_answer_columns_are_discovered_across_submissions():
    items = [{"results": {"name": "a"}}, {"results": {"name": "b", "topic": "t"}}]
    assert cli_resources._answer_fields(items) == ["name", "topic"]


def test_json_output_carries_the_submissions_verbatim(stubbed):
    out = _run("form", "submissions", "website_en", "--where", "topic=Celebrate", "--json")
    assert '"id": 1' in out


# ----------------------------------------------------------------------
# Command surface
# ----------------------------------------------------------------------

def test_resource_command_surface():
    assert _cmds(cli_resources.form) == {"list", "get", "submissions"}
    assert _cmds(cli_resources.contact) == {"list", "get"}
    assert _cmds(cli_resources.segment) == {"list", "get"}
    assert _cmds(cli_resources.campaign) == {"list", "get"}
    assert _cmds(cli_resources.email) == {"list", "get"}


def test_root_is_flat_with_status():
    assert {"form", "contact", "segment", "campaign", "email"} <= _groups(cli.app)
    assert "status" in _cmds(cli.app)


def test_config_requires_the_instance_and_both_credentials():
    import typer

    with pytest.raises(typer.Exit):
        cli._make_client({"mautic": {"base_url": "https://mautic.example.com"}})
    built = cli._make_client({"mautic": {
        "base_url": "https://mautic.example.com", "username": "u", "password": "p"}})
    assert built.instance_url == "https://mautic.example.com"
