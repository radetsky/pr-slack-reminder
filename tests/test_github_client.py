"""Tests for GitHub client: pagination and body-mention parsing."""

from datetime import datetime, timezone

import httpx
import pytest

from pr_bot.github_client import (
    _next_page_url,
    fetch_open_prs,
    fetch_pr_commits,
    fetch_pr_issue_comments,
    fetch_pr_reviews,
    latest_comment_per_login,
    latest_commit_at,
    latest_decisive_review_per_login,
    latest_review_per_login,
    make_client,
    parse_body_mentions,
)

CREATED = "2024-01-15T10:00:00Z"


def _pr_payload(number: int, draft: bool = False, reviewers: list[str] | None = None) -> dict:
    """Build a minimal GitHub PR API payload."""
    return {
        "number": number,
        "title": f"PR {number}",
        "html_url": f"https://github.com/owner/repo/pull/{number}",
        "user": {"login": "author"},
        "requested_reviewers": [{"login": r} for r in (reviewers or [])],
        "body": "",
        "draft": draft,
        "created_at": CREATED,
    }


def test_fetch_open_prs_single_page(httpx_mock):
    """Single-page response returns all PRs without pagination."""
    httpx_mock.add_response(json=[_pr_payload(1), _pr_payload(2)])

    with make_client("token") as client:
        prs = fetch_open_prs("owner/repo", client)

    assert len(prs) == 2
    assert prs[0].number == 1
    assert prs[1].number == 2


def test_fetch_open_prs_pagination(httpx_mock):
    """Multi-page response follows Link header to fetch all PRs."""
    page2_url = "https://api.github.com/repos/owner/repo/pulls?page=2"

    httpx_mock.add_response(
        json=[_pr_payload(1)],
        headers={"link": f'<{page2_url}>; rel="next"'},
    )
    httpx_mock.add_response(json=[_pr_payload(2)])

    with make_client("token") as client:
        prs = fetch_open_prs("owner/repo", client)

    assert len(prs) == 2
    assert prs[0].number == 1
    assert prs[1].number == 2


def test_fetch_open_prs_http_error(httpx_mock):
    """Non-2xx response raises HTTPStatusError."""
    httpx_mock.add_response(status_code=401, json={"message": "Bad credentials"})

    with make_client("bad-token") as client:
        with pytest.raises(httpx.HTTPStatusError):
            fetch_open_prs("owner/repo", client)


def test_parse_body_mentions_basic():
    """Extracts unique lowercase logins from body text."""
    logins = parse_body_mentions("Hey @Alice and @bob, please review. Also @Alice.")
    assert set(logins) == {"alice", "bob"}


def test_parse_body_mentions_empty_body():
    """Empty body returns empty list."""
    assert parse_body_mentions("") == []


def test_next_page_url_present():
    """Link header with 'next' returns the correct URL."""
    headers = {
        "link": '<https://api.github.com/repos/o/r/pulls?page=2>; rel="next", <...>; rel="last"'
    }
    response = httpx.Response(200, headers=headers)
    assert _next_page_url(response) == "https://api.github.com/repos/o/r/pulls?page=2"


def test_next_page_url_absent():
    """Link header without 'next' returns None."""
    headers = {"link": '<...>; rel="last"'}
    response = httpx.Response(200, headers=headers)
    assert _next_page_url(response) is None


def test_fetch_pr_reviews(httpx_mock):
    """Fetches raw review objects for a pull request."""
    httpx_mock.add_response(
        json=[{"user": {"login": "bob"}, "submitted_at": "2024-01-19T00:00:00Z", "state": "APPROVED"}]
    )

    with make_client("token") as client:
        reviews = fetch_pr_reviews("owner/repo", 1, client)

    assert len(reviews) == 1
    assert reviews[0]["user"]["login"] == "bob"


def test_fetch_pr_commits(httpx_mock):
    """Fetches raw commit objects for a pull request."""
    httpx_mock.add_response(
        json=[{"commit": {"committer": {"date": "2024-01-18T10:00:00Z"}}}]
    )

    with make_client("token") as client:
        commits = fetch_pr_commits("owner/repo", 1, client)

    assert len(commits) == 1


def test_latest_review_per_login_keeps_most_recent():
    """Multiple reviews from the same user collapse to the latest timestamp."""
    reviews = [
        {"user": {"login": "Bob"}, "submitted_at": "2024-01-18T00:00:00Z"},
        {"user": {"login": "bob"}, "submitted_at": "2024-01-19T00:00:00Z"},
    ]
    latest = latest_review_per_login(reviews)
    assert latest["bob"] == datetime(2024, 1, 19, 0, 0, tzinfo=timezone.utc)


def test_latest_review_per_login_ignores_pending_requests():
    """Reviews without submitted_at (e.g. pending) are skipped."""
    reviews = [{"user": {"login": "bob"}, "submitted_at": None}]
    assert latest_review_per_login(reviews) == {}


def test_fetch_pr_issue_comments(httpx_mock):
    """Fetches raw Conversation-tab comment objects for a pull request."""
    httpx_mock.add_response(
        json=[{"user": {"login": "bob"}, "created_at": "2024-01-19T00:00:00Z"}]
    )

    with make_client("token") as client:
        comments = fetch_pr_issue_comments("owner/repo", 1, client)

    assert len(comments) == 1
    assert comments[0]["user"]["login"] == "bob"


def test_latest_comment_per_login_keeps_most_recent():
    """Multiple comments from the same user collapse to the latest timestamp."""
    comments = [
        {"user": {"login": "Bob"}, "created_at": "2024-01-18T00:00:00Z"},
        {"user": {"login": "bob"}, "created_at": "2024-01-19T00:00:00Z"},
    ]
    latest = latest_comment_per_login(comments)
    assert latest["bob"] == datetime(2024, 1, 19, 0, 0, tzinfo=timezone.utc)


def test_latest_comment_per_login_empty_input():
    """No comments yields an empty mapping."""
    assert latest_comment_per_login([]) == {}


def test_latest_decisive_review_per_login_ignores_comments():
    """A COMMENTED-only review does not count as a decisive state."""
    reviews = [{"user": {"login": "bob"}, "submitted_at": "2024-01-19T00:00:00Z", "state": "COMMENTED"}]
    assert latest_decisive_review_per_login(reviews) == {}


def test_latest_decisive_review_per_login_keeps_latest_state():
    """A later APPROVED review overrides an earlier CHANGES_REQUESTED one."""
    reviews = [
        {"user": {"login": "bob"}, "submitted_at": "2024-01-18T00:00:00Z", "state": "CHANGES_REQUESTED"},
        {"user": {"login": "bob"}, "submitted_at": "2024-01-19T00:00:00Z", "state": "APPROVED"},
    ]
    assert latest_decisive_review_per_login(reviews) == {
        "bob": ("APPROVED", datetime(2024, 1, 19, 0, 0, tzinfo=timezone.utc))
    }


def test_latest_decisive_review_per_login_empty_input():
    """No reviews yields an empty mapping."""
    assert latest_decisive_review_per_login([]) == {}


def test_latest_commit_at_returns_max():
    """Returns the timestamp of the newest commit."""
    commits = [
        {"commit": {"committer": {"date": "2024-01-18T10:00:00Z"}}},
        {"commit": {"committer": {"date": "2024-01-19T10:00:00Z"}}},
    ]
    assert latest_commit_at(commits) == datetime(2024, 1, 19, 10, 0, tzinfo=timezone.utc)


def test_latest_commit_at_empty_list():
    """Returns None when there are no commits."""
    assert latest_commit_at([]) is None
