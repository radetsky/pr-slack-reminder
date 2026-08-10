"""Core reminder logic: map open PRs to Slack targets."""

import logging
from datetime import datetime

import httpx

from pr_bot.github_client import (
    fetch_open_prs,
    fetch_pr_commits,
    fetch_pr_reviews,
    latest_commit_at,
    latest_review_per_login,
    make_client,
    now_utc,
    parse_body_mentions,
)
from pr_bot.mapping import build_lookup, resolve_slack_id
from pr_bot.models import AppConfig, PullRequest, ReminderTarget

logger = logging.getLogger(__name__)


def _collect_targets_for_pr(
    pr: PullRequest,
    cfg: AppConfig,
    lookup: dict[str, str],
    reviewed_since_push: frozenset[str] = frozenset(),
) -> list[ReminderTarget]:
    """Determine which Slack users should be reminded about *pr*.

    Resolution order:
    1. Requested reviewers (GitHub clears this once they submit a review).
    2. If none and ping_author_if_no_reviewer is set → author.
    3. If parse_body_mentions is set → @mentions in PR body.

    Reviewers and body-mentioned logins already covered by
    *reviewed_since_push* (they left a review or comment after the PR's
    latest commit) are excluded, since re-pinging them would be noise until
    new changes land. The author is never excluded this way.

    Duplicates are collapsed by Slack ID (or login for unmapped users).

    Args:
        pr: The pull request to process.
        cfg: Application configuration with reminder flags.
        lookup: GitHub-login → Slack-ID lookup dict.
        reviewed_since_push: Lowercase GitHub logins who already reviewed or
            commented after the PR's most recent commit.

    Returns:
        List of ReminderTarget instances (one per unique Slack user).
    """
    logins: list[str] = [
        login for login in pr.reviewer_logins if login.lower() not in reviewed_since_push
    ]

    if not pr.reviewer_logins and cfg.reminders.ping_author_if_no_reviewer:
        logins.append(pr.author_login)

    if cfg.reminders.parse_body_mentions:
        body_logins = parse_body_mentions(pr.body)
        for login in body_logins:
            if login not in logins and login.lower() not in reviewed_since_push:
                logins.append(login)

    seen: dict[str, ReminderTarget] = {}
    for login in logins:
        slack_id, display = resolve_slack_id(login, lookup)
        key = slack_id or login.lower()
        if key not in seen:
            seen[key] = ReminderTarget(
                slack_id=slack_id or "",
                display=display,
                pull_requests=[],
            )
        seen[key].pull_requests.append(pr)

    return list(seen.values())


def _reviewed_since_push(pr: PullRequest, repo: str, client: httpx.Client) -> frozenset[str]:
    """Determine which reviewers already responded after the PR's latest commit.

    Args:
        pr: The pull request to inspect.
        repo: Repository in 'owner/name' format.
        client: Authenticated httpx.Client instance.

    Returns:
        Lowercase GitHub logins whose most recent review or comment is at
        least as new as the PR's latest commit.
    """
    reviews = fetch_pr_reviews(repo, pr.number, client)
    commits = fetch_pr_commits(repo, pr.number, client)
    last_push = latest_commit_at(commits)
    if last_push is None:
        return frozenset()

    return frozenset(
        login
        for login, submitted_at in latest_review_per_login(reviews).items()
        if submitted_at >= last_push
    )


def build_reminder_targets(
    cfg: AppConfig, username_to_id: dict[str, str]
) -> list[ReminderTarget]:
    """Fetch PRs for all configured repositories and build reminder targets.

    Applies draft and stale-age filters according to cfg.reminders, then
    collects ReminderTarget objects merging all PRs per Slack user.

    Args:
        cfg: Fully loaded application configuration.
        username_to_id: Workspace-wide lowercase Slack username -> Slack user ID,
            as returned by :func:`pr_bot.slack_client.fetch_workspace_usernames`.

    Returns:
        List of ReminderTarget instances (merged across all repos).

    Raises:
        httpx.HTTPStatusError: On GitHub API auth or rate-limit failures.
    """
    lookup = build_lookup(cfg.users, username_to_id)
    now: datetime = now_utc()

    merged: dict[str, ReminderTarget] = {}

    with make_client(cfg.github_token) as client:
        for repo in cfg.repositories:
            try:
                prs = fetch_open_prs(repo, client)
            except Exception as exc:
                logger.error("Failed to fetch PRs for %s: %s", repo, exc)
                raise

            for pr in prs:
                if cfg.reminders.ignore_drafts and pr.draft:
                    logger.debug("Skipping draft PR #%d in %s", pr.number, repo)
                    continue

                if cfg.reminders.stale_hours > 0:
                    if pr.age_hours(now) < cfg.reminders.stale_hours:
                        logger.debug(
                            "Skipping PR #%d in %s (not stale enough)", pr.number, repo
                        )
                        continue

                reviewed_since_push = _reviewed_since_push(pr, repo, client)
                targets = _collect_targets_for_pr(pr, cfg, lookup, reviewed_since_push)
                for target in targets:
                    key = target.slack_id or target.display
                    if key not in merged:
                        merged[key] = ReminderTarget(
                            slack_id=target.slack_id,
                            display=target.display,
                        )
                    merged[key].pull_requests.extend(target.pull_requests)

    return list(merged.values())
