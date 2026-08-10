"""GitHub REST API client: fetch open pull requests with pagination."""

import logging
import re
from datetime import datetime, timezone

import httpx

from pr_bot.models import PullRequest

logger = logging.getLogger(__name__)

BODY_MENTION_RE = re.compile(r"@([A-Za-z0-9][A-Za-z0-9-]*)")

_GITHUB_API_BASE = "https://api.github.com"
_PER_PAGE = 100


def _parse_pr(repo: str, data: dict) -> PullRequest:
    """Convert a raw GitHub PR dict to a PullRequest dataclass.

    Args:
        repo: Repository in 'owner/name' format.
        data: Single PR object from GitHub REST response.

    Returns:
        PullRequest instance.
    """
    reviewers = [r["login"] for r in data.get("requested_reviewers", [])]
    created_raw = data["created_at"]
    created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    return PullRequest(
        number=data["number"],
        title=data["title"],
        url=data["html_url"],
        repo=repo,
        author_login=data["user"]["login"],
        reviewer_logins=reviewers,
        body=data.get("body") or "",
        draft=data.get("draft", False),
        created_at=created_at,
    )


def _fetch_paginated(url: str, client: httpx.Client, params: dict | None = None) -> list[dict]:
    """Fetch every page of a GitHub list endpoint, following Link headers.

    Args:
        url: Initial request URL.
        client: Authenticated httpx.Client instance.
        params: Query params for the first request only.

    Returns:
        Concatenated JSON items from all pages.

    Raises:
        httpx.HTTPStatusError: On non-2xx GitHub responses (propagated to caller).
    """
    items: list[dict] = []
    next_url: str | None = url
    next_params = {"per_page": _PER_PAGE, **(params or {})}

    while next_url:
        response = client.get(next_url, params=next_params)
        response.raise_for_status()
        items.extend(response.json())
        next_params = {}
        next_url = _next_page_url(response)

    return items


def fetch_open_prs(repo: str, client: httpx.Client) -> list[PullRequest]:
    """Fetch all open (non-paginated) pull requests for *repo*.

    Handles GitHub's Link-based pagination automatically.

    Args:
        repo: Repository slug in 'owner/name' format.
        client: Authenticated httpx.Client instance.

    Returns:
        List of PullRequest objects for all open PRs.

    Raises:
        httpx.HTTPStatusError: On non-2xx GitHub responses (propagated to caller).
    """
    url = f"{_GITHUB_API_BASE}/repos/{repo}/pulls"
    items = _fetch_paginated(url, client, params={"state": "open"})
    prs = [_parse_pr(repo, item) for item in items]

    logger.debug("Fetched %d open PRs from %s", len(prs), repo)
    return prs


def fetch_pr_reviews(repo: str, number: int, client: httpx.Client) -> list[dict]:
    """Fetch all reviews submitted on a pull request.

    Args:
        repo: Repository in 'owner/name' format.
        number: Pull request number.
        client: Authenticated httpx.Client instance.

    Returns:
        Raw review objects as returned by the GitHub REST API.

    Raises:
        httpx.HTTPStatusError: On non-2xx GitHub responses (propagated to caller).
    """
    url = f"{_GITHUB_API_BASE}/repos/{repo}/pulls/{number}/reviews"
    return _fetch_paginated(url, client)


def fetch_pr_commits(repo: str, number: int, client: httpx.Client) -> list[dict]:
    """Fetch all commits belonging to a pull request.

    Args:
        repo: Repository in 'owner/name' format.
        number: Pull request number.
        client: Authenticated httpx.Client instance.

    Returns:
        Raw commit objects as returned by the GitHub REST API.

    Raises:
        httpx.HTTPStatusError: On non-2xx GitHub responses (propagated to caller).
    """
    url = f"{_GITHUB_API_BASE}/repos/{repo}/pulls/{number}/commits"
    return _fetch_paginated(url, client)


def latest_review_per_login(reviews: list[dict]) -> dict[str, datetime]:
    """Return each reviewer's most recent review submission time.

    Args:
        reviews: Raw review objects from :func:`fetch_pr_reviews`.

    Returns:
        Lowercase GitHub login -> timestamp of that user's latest review.
    """
    latest: dict[str, datetime] = {}
    for review in reviews:
        submitted_raw = review.get("submitted_at")
        if not submitted_raw:
            continue
        login = review["user"]["login"].lower()
        submitted_at = datetime.fromisoformat(submitted_raw.replace("Z", "+00:00"))
        if login not in latest or submitted_at > latest[login]:
            latest[login] = submitted_at
    return latest


def latest_commit_at(commits: list[dict]) -> datetime | None:
    """Return the timestamp of the most recent commit in a commit list.

    Args:
        commits: Raw commit objects from :func:`fetch_pr_commits`.

    Returns:
        Timestamp of the newest commit, or None if *commits* is empty.
    """
    timestamps = [
        datetime.fromisoformat(commit["commit"]["committer"]["date"].replace("Z", "+00:00"))
        for commit in commits
    ]
    return max(timestamps) if timestamps else None


def _next_page_url(response: httpx.Response) -> str | None:
    """Extract the 'next' page URL from a GitHub Link header, if present.

    Args:
        response: HTTP response from GitHub.

    Returns:
        URL string for the next page, or None if this is the last page.
    """
    link_header = response.headers.get("link", "")
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            url_part = part.split(";")[0].strip()
            return url_part.strip("<>")
    return None


def parse_body_mentions(body: str) -> list[str]:
    """Extract unique GitHub @mentions from a PR body.

    Args:
        body: PR description text (may be empty string).

    Returns:
        Deduplicated list of lowercase GitHub logins found in the body.
    """
    return list({m.lower() for m in BODY_MENTION_RE.findall(body)})


def make_client(token: str) -> httpx.Client:
    """Create a pre-configured httpx.Client for GitHub REST API calls.

    Args:
        token: GitHub personal access token (fine-grained PAT).

    Returns:
        Configured httpx.Client with auth headers and timeout.
    """
    return httpx.Client(
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    )


def now_utc() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Returns:
        Current UTC datetime.
    """
    return datetime.now(tz=timezone.utc)
