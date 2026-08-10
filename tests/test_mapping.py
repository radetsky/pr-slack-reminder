"""Tests for GitHub → Slack user mapping resolution."""

import logging

from pr_bot.mapping import build_lookup, resolve_slack_id
from pr_bot.models import UserMapping


def _users() -> list[UserMapping]:
    return [
        UserMapping(github="alice", slack_username="alice.smith"),
        UserMapping(github="Bob", slack_username="bob.jones"),
    ]


def _username_to_id() -> dict[str, str]:
    return {"alice.smith": "U111", "bob.jones": "U222"}


def test_build_lookup_lowercases_github():
    """Keys in the lookup are always lowercase."""
    lookup = build_lookup(_users(), _username_to_id())
    assert "alice" in lookup
    assert "bob" in lookup


def test_build_lookup_resolves_username_to_id():
    """Configured Slack usernames resolve to workspace member IDs."""
    lookup = build_lookup(_users(), _username_to_id())
    assert lookup["alice"] == "U111"
    assert lookup["bob"] == "U222"


def test_build_lookup_skips_unresolved_username(caplog):
    """A Slack username missing from the workspace is skipped with a warning."""
    users = [UserMapping(github="alice", slack_username="not.in.workspace")]
    with caplog.at_level(logging.WARNING, logger="pr_bot.mapping"):
        lookup = build_lookup(users, _username_to_id())
    assert "alice" not in lookup
    assert "not.in.workspace" in caplog.text


def test_resolve_known_user():
    """Known GitHub login resolves to Slack ID and <@ID> display."""
    lookup = build_lookup(_users(), _username_to_id())
    slack_id, display = resolve_slack_id("Alice", lookup)
    assert slack_id == "U111"
    assert display == "<@U111>"


def test_resolve_unknown_user_returns_none(caplog):
    """Unknown login returns (None, login) and logs a warning."""
    lookup = build_lookup(_users(), _username_to_id())
    with caplog.at_level(logging.WARNING, logger="pr_bot.mapping"):
        slack_id, display = resolve_slack_id("charlie", lookup)
    assert slack_id is None
    assert display == "charlie"
    assert "charlie" in caplog.text
