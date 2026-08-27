"""Core reminder logic: map open PRs to Slack targets."""

import logging
from dataclasses import replace
from datetime import datetime
from typing import NamedTuple

import httpx

from pr_bot.github_client import (
    fetch_open_prs,
    fetch_pr_commits,
    fetch_pr_issue_comments,
    fetch_pr_reviews,
    latest_comment_per_login,
    latest_commit_at,
    latest_decisive_review_per_login,
    latest_review_per_login,
    make_client,
    now_utc,
    parse_body_mentions,
)
from pr_bot.mapping import build_lookup, resolve_slack_id
from pr_bot.models import AppConfig, PullRequest, ReminderTarget, ReviewStatus

logger = logging.getLogger(__name__)


class ReviewSignal(NamedTuple):
    """Outcome of inspecting a PR's reviews, commits, and comments."""

    satisfied: frozenset[str]
    status: ReviewStatus
    reopened: frozenset[str]


def _should_ping(login: str, signal: ReviewSignal) -> bool:
    """Decide whether *login* still owes a look, given *signal*.

    Once the PR has a standing approval (`ReviewStatus.APPROVED`), the PR is
    treated as reviewed and nobody else is chased for a first look -- only the
    author is, plus anyone explicitly reopened by a later @-mention. Otherwise
    a login is pinged unless it's in `signal.satisfied`.

    Args:
        login: GitHub login to check (any case).
        signal: Review outcome from `_reviewed_since_push`.

    Returns:
        True if *login* should still be pinged.
    """
    login_lc = login.lower()
    if login_lc in signal.reopened:
        return True
    if signal.status is ReviewStatus.APPROVED:
        return False
    return login_lc not in signal.satisfied


def _collect_targets_for_pr(
    pr: PullRequest,
    cfg: AppConfig,
    lookup: dict[str, str],
    signal: ReviewSignal = ReviewSignal(frozenset(), ReviewStatus.PENDING, frozenset()),
) -> list[ReminderTarget]:
    """Determine which Slack users should be reminded about *pr*.

    Resolution order:
    1. Requested reviewers (GitHub clears this once they submit a review).
    2. If none, or the PR already has a standing approval, and
       ping_author_if_no_reviewer is set → author.
    3. If parse_body_mentions is set → @mentions in PR body.

    Reviewers and body-mentioned logins are filtered through `_should_ping`:
    once the PR has a standing approval it's treated as reviewed and only the
    author (plus anyone explicitly reopened by a later @-mention) is pinged,
    regardless of whether some other requested reviewer never responded at
    all. Short of a standing approval, a login is skipped only while it's
    covered by `signal.satisfied`. The author is never excluded this way.

    Duplicates are collapsed by Slack ID (or login for unmapped users).

    Args:
        pr: The pull request to process.
        cfg: Application configuration with reminder flags.
        lookup: GitHub-login → Slack-ID lookup dict.
        signal: Review outcome from `_reviewed_since_push`.

    Returns:
        List of ReminderTarget instances (one per unique Slack user).
    """
    logins: list[str] = [login for login in pr.reviewer_logins if _should_ping(login, signal)]

    if (
        not pr.reviewer_logins or signal.status is ReviewStatus.APPROVED
    ) and cfg.reminders.ping_author_if_no_reviewer:
        logins.append(pr.author_login)

    if cfg.reminders.parse_body_mentions:
        body_logins = parse_body_mentions(pr.body)
        for login in body_logins:
            if login not in logins and _should_ping(login, signal):
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


def _remmentioned_after(login: str, since: datetime, comments: list[dict]) -> bool:
    """Check whether *login* was explicitly @-mentioned in a comment after *since*.

    Used to reopen the ping for someone who already approved: their approval
    stands indefinitely, but an explicit callout addressed to them later in
    the thread should still reach them.

    Args:
        login: Lowercase GitHub login to look for.
        since: Only comments strictly after this timestamp are considered.
        comments: Raw issue comment objects from :func:`fetch_pr_issue_comments`.

    Returns:
        True if a comment after *since* contains an "@login" mention.
    """
    for comment in comments:
        created_raw = comment.get("created_at")
        if not created_raw:
            continue
        created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        if created_at > since and login in parse_body_mentions(comment.get("body") or ""):
            return True
    return False


def _reviewed_since_push(pr: PullRequest, repo: str, client: httpx.Client) -> ReviewSignal:
    """Determine who is satisfied on *pr* and its aggregate review status.

    A login who approved is satisfied indefinitely -- new commits don't need
    their attention again, since GitHub itself doesn't require re-approval --
    unless a later comment explicitly @-mentions them, which reopens the ping.

    A login who has not approved (never reviewed, only commented, or
    requested changes) is satisfied only while their latest review or plain
    Conversation-tab comment is at least as new as the PR's latest commit;
    once a new commit lands they are owed another look. Comments are checked
    in addition to formal reviews because GitHub only clears
    `requested_reviewers` for the latter, so a reviewer who only left a
    Conversation-tab comment would otherwise be re-pinged forever.

    Approved means at least one reviewer's latest decisive review (ignoring
    plain comments) is APPROVED, that approval was not reopened by a later
    @-mention, and none is CHANGES_REQUESTED. Needs-work means no reviewer is
    still owed a first review, nobody has a standing approval, but at least
    one reviewer already responded since the latest commit.

    Args:
        pr: The pull request to inspect.
        repo: Repository in 'owner/name' format.
        client: Authenticated httpx.Client instance.

    Returns:
        ReviewSignal with the satisfied logins, aggregate status, and any
        logins reopened by a later @-mention after their approval.
    """
    reviews = fetch_pr_reviews(repo, pr.number, client)
    commits = fetch_pr_commits(repo, pr.number, client)
    comments = fetch_pr_issue_comments(repo, pr.number, client)
    last_push = latest_commit_at(commits)

    if last_push is None:
        recency_satisfied = frozenset()
    else:
        review_responded = {
            login
            for login, submitted_at in latest_review_per_login(reviews).items()
            if submitted_at >= last_push
        }
        comment_responded = {
            login
            for login, commented_at in latest_comment_per_login(comments).items()
            if commented_at >= last_push
        }
        recency_satisfied = review_responded | comment_responded

    decisive = latest_decisive_review_per_login(reviews)
    approved_logins = {login for login, (state, _) in decisive.items() if state == "APPROVED"}
    has_changes_requested = any(state == "CHANGES_REQUESTED" for state, _ in decisive.values())

    reopened_logins = {
        login
        for login in approved_logins
        if _remmentioned_after(login, decisive[login][1], comments)
    }
    standing_approved = approved_logins - reopened_logins

    responded = frozenset((recency_satisfied | approved_logins) - reopened_logins)

    if standing_approved and not has_changes_requested:
        status = ReviewStatus.APPROVED
    elif not pr.reviewer_logins and responded:
        status = ReviewStatus.NEEDS_WORK
    else:
        status = ReviewStatus.PENDING

    return ReviewSignal(satisfied=responded, status=status, reopened=frozenset(reopened_logins))


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

                signal = _reviewed_since_push(pr, repo, client)
                pr = replace(pr, review_status=signal.status)
                targets = _collect_targets_for_pr(pr, cfg, lookup, signal)
                for target in targets:
                    key = target.slack_id or target.display
                    if key not in merged:
                        merged[key] = ReminderTarget(
                            slack_id=target.slack_id,
                            display=target.display,
                        )
                    merged[key].pull_requests.extend(target.pull_requests)

    return list(merged.values())
