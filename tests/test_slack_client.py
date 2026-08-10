"""Tests for Slack workspace username resolution."""

from unittest.mock import patch

import pytest
from slack_sdk.errors import SlackApiError

from pr_bot.slack_client import fetch_workspace_usernames


def _member(name: str, user_id: str, *, deleted: bool = False, is_bot: bool = False) -> dict:
    return {"id": user_id, "name": name, "deleted": deleted, "is_bot": is_bot}


def test_fetch_workspace_usernames_lowercases_names():
    """Returned usernames are lowercased for case-insensitive lookup."""
    page = {"members": [_member("Alice.Smith", "U111")]}
    with patch("pr_bot.slack_client.WebClient.users_list", return_value=[page]):
        result = fetch_workspace_usernames("xoxb-test")
    assert result == {"alice.smith": "U111"}


def test_fetch_workspace_usernames_skips_deleted_and_bots():
    """Deactivated members and bot users are excluded."""
    page = {
        "members": [
            _member("alice", "U111"),
            _member("gone", "U222", deleted=True),
            _member("reminderbot", "U333", is_bot=True),
        ]
    }
    with patch("pr_bot.slack_client.WebClient.users_list", return_value=[page]):
        result = fetch_workspace_usernames("xoxb-test")
    assert result == {"alice": "U111"}


def test_fetch_workspace_usernames_also_indexes_display_name():
    """A member is resolvable by both their legacy username and display name."""
    member = _member("alexey.radetsky", "U111")
    member["profile"] = {"display_name": "rad"}
    page = {"members": [member]}
    with patch("pr_bot.slack_client.WebClient.users_list", return_value=[page]):
        result = fetch_workspace_usernames("xoxb-test")
    assert result == {"alexey.radetsky": "U111", "rad": "U111"}


def test_fetch_workspace_usernames_merges_multiple_pages():
    """Members across paginated responses are all collected."""
    pages = [
        {"members": [_member("alice", "U111")]},
        {"members": [_member("bob", "U222")]},
    ]
    with patch("pr_bot.slack_client.WebClient.users_list", return_value=pages):
        result = fetch_workspace_usernames("xoxb-test")
    assert result == {"alice": "U111", "bob": "U222"}


def test_fetch_workspace_usernames_exits_on_api_error():
    """A Slack API error (e.g. missing users:read scope) exits the process."""
    error = SlackApiError("missing_scope", {"error": "missing_scope"})
    with patch("pr_bot.slack_client.WebClient.users_list", side_effect=error):
        with pytest.raises(SystemExit):
            fetch_workspace_usernames("xoxb-test")
