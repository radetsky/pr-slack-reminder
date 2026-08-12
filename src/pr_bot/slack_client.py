"""Slack message building and delivery via Block Kit."""

import json
import logging
import sys
from datetime import datetime

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from pr_bot.github_client import now_utc
from pr_bot.models import PullRequest, ReminderTarget

logger = logging.getLogger(__name__)

# (minimum age in days, emoji) pairs, checked from oldest to youngest.
_AGE_TIERS: list[tuple[int, str]] = [
    (60, "skull"),
    (30, "red_circle"),
    (14, "large_orange_circle"),
    (7, "large_yellow_circle"),
    (0, "large_green_circle"),
]


def fetch_workspace_usernames(token: str) -> dict[str, str]:
    """Fetch all active, non-bot Slack workspace members and their usernames.

    Indexes each member under both their legacy username (`name`) and their
    profile display name, since either may be what a human calls their
    "Slack username" — and the two can differ.

    Requires the bot token to have the `users:read` OAuth scope.

    Args:
        token: Slack bot OAuth token.

    Returns:
        Dict mapping lowercase Slack username/display name to Slack user ID.

    Raises:
        SystemExit: On Slack API errors (e.g. missing scope, invalid token).
    """
    client = WebClient(token=token)
    username_to_id: dict[str, str] = {}
    try:
        for page in client.users_list(limit=200):
            for member in page.get("members", []):
                if member.get("deleted") or member.get("is_bot"):
                    continue
                for username in (member.get("name"), member.get("profile", {}).get("display_name")):
                    if username:
                        username_to_id[username.lower()] = member["id"]
    except SlackApiError as exc:
        logger.error("Failed to list Slack workspace users: %s", exc.response["error"])
        print(f"ERROR: Slack API error while listing users: {exc.response['error']}", file=sys.stderr)
        sys.exit(1)

    logger.debug("Fetched %d active Slack workspace user(s).", len(username_to_id))
    return username_to_id


def _mention(target: ReminderTarget) -> str:
    """Return an inline Slack mention string for *target*.

    Uses <@USERID> when a Slack ID is known, otherwise falls back to the
    plain GitHub login so no-one is silently dropped.

    Args:
        target: Reminder target with optional Slack ID.

    Returns:
        Slack mention string.
    """
    if target.slack_id:
        return f"<@{target.slack_id}>"
    return f"`@{target.display}`"


def _age_emoji(age_days: float) -> str:
    """Return the age-tier emoji name for a PR *age_days* old.

    Args:
        age_days: PR age in days.

    Returns:
        Emoji name (without colons) for the oldest tier the PR has reached.
    """
    for min_days, emoji in _AGE_TIERS:
        if age_days >= min_days:
            return emoji
    return _AGE_TIERS[-1][1]


def _pr_line(pr: PullRequest, now: datetime) -> str:
    """Return the mrkdwn bullet line for *pr*, prefixed with an age-tier emoji.

    Args:
        pr: The pull request to render.
        now: Current time, used to compute PR age.

    Returns:
        Single mrkdwn bullet line.
    """
    age_days = pr.age_hours(now) / 24
    emoji = _age_emoji(age_days)
    link = f"<{pr.url}|{pr.repo}#{pr.number}: {pr.title}>"
    return f"• :{emoji}: {link} _({int(age_days)} days)_"


def _pr_counts(targets: list[ReminderTarget]) -> tuple[int, int]:
    """Return the total PR mentions across *targets* and the count of distinct PRs.

    A PR requested from two reviewers counts twice toward the total but once
    toward the unique count.

    Args:
        targets: Reminder targets to count PRs across.

    Returns:
        Tuple of (total mentions, distinct PR count).
    """
    total = sum(len(target.pull_requests) for target in targets)
    unique = {(pr.repo, pr.number) for target in targets for pr in target.pull_requests}
    return total, len(unique)


def build_blocks(targets: list[ReminderTarget]) -> list[dict]:
    """Build Slack Block Kit blocks for the PR review digest message.

    Sections are ordered by descending PR count, so the users with the most
    pending reviews appear first. Within each section, PRs are ordered by
    descending age across all repositories. Each PR line is prefixed with an
    age-tier emoji (green/yellow/orange/red/skull) and its age in days. The
    header reports the total number of PR mentions, plus the number of
    distinct PRs when that differs from the total.

    Args:
        targets: Non-empty list of ReminderTarget instances.

    Returns:
        List of Block Kit block dicts ready for chat.postMessage.
    """
    sorted_targets = sorted(targets, key=lambda t: len(t.pull_requests), reverse=True)
    now = now_utc()

    total, unique = _pr_counts(sorted_targets)
    count_line = f"*{total} PR{'s' if total != 1 else ''}* to review"
    if unique != total:
        count_line += f" ({unique} unique)"

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    ":wave: *PR review reminder* — when you get a moment, these pull requests "
                    f"could use your review. Thanks for helping keep things moving!\n{count_line}"
                ),
            },
        },
        {"type": "divider"},
    ]

    for target in sorted_targets:
        prs_by_age = sorted(target.pull_requests, key=lambda pr: pr.age_hours(now), reverse=True)
        pr_lines = [_pr_line(pr, now) for pr in prs_by_age]
        prs_text = "\n".join(pr_lines)
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{_mention(target)}\n{prs_text}",
                },
            }
        )

    blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Much appreciated :pray:",
            },
        }
    )

    return blocks


def send_or_print(
    targets: list[ReminderTarget],
    channel: str,
    slack_token: str,
    dry_run: bool,
) -> None:
    """Send a digest message to Slack, or print it to stdout in dry-run mode.

    Args:
        targets: Reminder targets to include in the digest.
        channel: Slack channel ID or name.
        slack_token: Bot OAuth token with chat:write scope.
        dry_run: When True, print the payload to stdout instead of sending.

    Raises:
        SystemExit: On Slack API errors in live mode.
    """
    if not targets:
        logger.info("No pending PR reviews found — nothing to send.")
        if dry_run:
            print("No pending PR reviews found.")
        return

    blocks = build_blocks(targets)

    if dry_run:
        print("=== DRY RUN — would send to Slack channel:", channel, "===")
        print(json.dumps(blocks, indent=2, ensure_ascii=False))
        return

    client = WebClient(token=slack_token)
    try:
        client.chat_postMessage(channel=channel, blocks=blocks, text="PR review reminder")
        logger.info("Reminder sent to %s for %d user(s).", channel, len(targets))
    except SlackApiError as exc:
        logger.error("Slack API error: %s", exc.response["error"])
        print(f"ERROR: Slack API error: {exc.response['error']}", file=sys.stderr)
        sys.exit(1)
