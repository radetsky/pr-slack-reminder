"""Tests for core reminder logic: filtering and target collection."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from pr_bot.models import AppConfig, PullRequest, RemindersConfig, UserMapping
from pr_bot.reminder import _collect_targets_for_pr, build_reminder_targets

USERS = [
    UserMapping(github="alice", slack_username="alice.smith"),
    UserMapping(github="bob", slack_username="bob.jones"),
]

USERNAME_TO_ID = {"alice.smith": "U111", "bob.jones": "U222"}

NOW = datetime(2024, 1, 20, 12, 0, 0, tzinfo=timezone.utc)
OLD_CREATED = datetime(2024, 1, 18, 10, 0, 0, tzinfo=timezone.utc)


def _make_cfg(**kwargs) -> AppConfig:
    """Build a minimal AppConfig with overridable reminder flags."""
    reminders = RemindersConfig(
        ignore_drafts=kwargs.pop("ignore_drafts", True),
        ping_author_if_no_reviewer=kwargs.pop("ping_author_if_no_reviewer", True),
        parse_body_mentions=kwargs.pop("parse_body_mentions", True),
        stale_hours=kwargs.pop("stale_hours", 0),
    )
    return AppConfig(
        slack_channel="C123",
        repositories=["owner/repo"],
        reminders=reminders,
        users=USERS,
        github_token="ghp_test",
        slack_token="xoxb-test",
    )


def _make_pr(**kwargs) -> PullRequest:
    """Build a PullRequest with sensible defaults."""
    return PullRequest(
        number=kwargs.get("number", 1),
        title=kwargs.get("title", "Test PR"),
        url="https://github.com/owner/repo/pull/1",
        repo="owner/repo",
        author_login=kwargs.get("author_login", "alice"),
        reviewer_logins=kwargs.get("reviewer_logins", []),
        body=kwargs.get("body", ""),
        draft=kwargs.get("draft", False),
        created_at=kwargs.get("created_at", OLD_CREATED),
    )


def _lookup() -> dict[str, str]:
    from pr_bot.mapping import build_lookup
    return build_lookup(USERS, USERNAME_TO_ID)


def test_collect_targets_uses_reviewers():
    """Assigned reviewers are the primary ping targets."""
    pr = _make_pr(reviewer_logins=["bob"])
    targets = _collect_targets_for_pr(pr, _make_cfg(), _lookup())
    assert len(targets) == 1
    assert targets[0].slack_id == "U222"


def test_collect_targets_falls_back_to_author_when_no_reviewer():
    """Author is pinged when no reviewers are assigned."""
    pr = _make_pr(reviewer_logins=[], author_login="alice")
    targets = _collect_targets_for_pr(pr, _make_cfg(ping_author_if_no_reviewer=True), _lookup())
    assert any(t.slack_id == "U111" for t in targets)


def test_collect_targets_no_author_fallback_when_disabled():
    """Author is not pinged when ping_author_if_no_reviewer is False."""
    pr = _make_pr(reviewer_logins=[], author_login="alice")
    targets = _collect_targets_for_pr(
        pr, _make_cfg(ping_author_if_no_reviewer=False), _lookup()
    )
    assert len(targets) == 0


def test_collect_targets_body_mentions():
    """@mentions in PR body are included when parse_body_mentions is True."""
    pr = _make_pr(reviewer_logins=[], body="pls review @bob")
    targets = _collect_targets_for_pr(
        pr, _make_cfg(ping_author_if_no_reviewer=False), _lookup()
    )
    assert any(t.slack_id == "U222" for t in targets)


def test_collect_targets_deduplicates():
    """Same user appearing as reviewer and in body is added only once."""
    pr = _make_pr(reviewer_logins=["bob"], body="cc @bob")
    targets = _collect_targets_for_pr(pr, _make_cfg(), _lookup())
    assert len(targets) == 1


def test_collect_targets_excludes_reviewer_who_reviewed_since_push():
    """A reviewer who already reviewed after the latest commit is not re-pinged."""
    pr = _make_pr(reviewer_logins=["bob"])
    targets = _collect_targets_for_pr(
        pr, _make_cfg(), _lookup(), reviewed_since_push=frozenset({"bob"})
    )
    assert targets == []


def test_collect_targets_excludes_body_mention_reviewed_since_push():
    """A body-mentioned user who already commented since the last push is skipped."""
    pr = _make_pr(reviewer_logins=[], body="cc @bob")
    targets = _collect_targets_for_pr(
        pr,
        _make_cfg(ping_author_if_no_reviewer=False),
        _lookup(),
        reviewed_since_push=frozenset({"bob"}),
    )
    assert targets == []


def test_collect_targets_author_fallback_ignores_reviewed_since_push():
    """The ping_author_if_no_reviewer fallback is unaffected by reviewed_since_push."""
    pr = _make_pr(reviewer_logins=[], author_login="alice")
    targets = _collect_targets_for_pr(
        pr,
        _make_cfg(ping_author_if_no_reviewer=True),
        _lookup(),
        reviewed_since_push=frozenset({"alice"}),
    )
    assert any(t.slack_id == "U111" for t in targets)


def test_pr_age_hours():
    """age_hours returns correct float."""
    pr = _make_pr(created_at=OLD_CREATED)
    hours = pr.age_hours(NOW)
    assert abs(hours - 50.0) < 0.01


def _pr_api_payload(
    number: int = 1, draft: bool = False, reviewers: list[str] | None = None
) -> dict:
    """Build a minimal GitHub PR API payload for integration-style tests."""
    return {
        "number": number,
        "title": f"PR {number}",
        "html_url": f"https://github.com/owner/repo/pull/{number}",
        "user": {"login": "alice"},
        "requested_reviewers": [{"login": r} for r in (reviewers or ["bob"])],
        "body": "",
        "draft": draft,
        "created_at": "2024-01-18T10:00:00Z",
    }


def test_build_reminder_targets_skips_drafts(httpx_mock):
    """Draft PRs are not included in targets when ignore_drafts=True."""
    httpx_mock.add_response(json=[_pr_api_payload(draft=True)])

    with patch("pr_bot.reminder.now_utc", return_value=NOW):
        targets = build_reminder_targets(_make_cfg(ignore_drafts=True), USERNAME_TO_ID)

    assert targets == []


def test_build_reminder_targets_includes_non_draft(httpx_mock):
    """Non-draft PR with a reviewer yields one target."""
    httpx_mock.add_response(json=[_pr_api_payload(draft=False)])
    httpx_mock.add_response(json=[])  # reviews
    httpx_mock.add_response(json=[])  # commits

    with patch("pr_bot.reminder.now_utc", return_value=NOW):
        targets = build_reminder_targets(_make_cfg(), USERNAME_TO_ID)

    assert len(targets) == 1
    assert targets[0].slack_id == "U222"


def test_build_reminder_targets_excludes_reviewer_who_already_reviewed(httpx_mock):
    """Reviewer whose review postdates the latest commit is not re-pinged."""
    httpx_mock.add_response(json=[_pr_api_payload(draft=False)])
    httpx_mock.add_response(
        json=[{"user": {"login": "bob"}, "submitted_at": "2024-01-19T00:00:00Z"}]
    )
    httpx_mock.add_response(json=[{"commit": {"committer": {"date": "2024-01-18T10:00:00Z"}}}])

    with patch("pr_bot.reminder.now_utc", return_value=NOW):
        targets = build_reminder_targets(_make_cfg(ping_author_if_no_reviewer=False), USERNAME_TO_ID)

    assert targets == []


def test_build_reminder_targets_stale_filter(httpx_mock):
    """PRs younger than stale_hours threshold are excluded."""
    fresh_payload = {**_pr_api_payload(), "created_at": "2024-01-20T11:00:00Z"}
    httpx_mock.add_response(json=[fresh_payload])

    with patch("pr_bot.reminder.now_utc", return_value=NOW):
        targets = build_reminder_targets(_make_cfg(stale_hours=48), USERNAME_TO_ID)

    assert targets == []
