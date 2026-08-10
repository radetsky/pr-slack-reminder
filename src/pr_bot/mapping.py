"""GitHub login → Slack user ID resolution with graceful miss handling."""

import logging

from pr_bot.models import UserMapping

logger = logging.getLogger(__name__)


def build_lookup(users: list[UserMapping], username_to_id: dict[str, str]) -> dict[str, str]:
    """Build a GitHub-login -> Slack-ID lookup by resolving configured usernames.

    Config entries whose Slack username is not found in *username_to_id* (e.g.
    a typo, or the user left the workspace) are skipped with a warning rather
    than failing the whole run.

    Args:
        users: List of UserMapping instances from config (GitHub login -> Slack username).
        username_to_id: Workspace-wide lowercase Slack username -> Slack user ID,
            as returned by :func:`pr_bot.slack_client.fetch_workspace_usernames`.

    Returns:
        Dict mapping lowercase GitHub login to Slack user ID.
    """
    lookup: dict[str, str] = {}
    for user in users:
        slack_id = username_to_id.get(user.slack_username.lower())
        if slack_id is None:
            logger.warning(
                "Slack username %r (mapped from GitHub %r) not found in workspace — "
                "will use plain-text mention.",
                user.slack_username,
                user.github,
            )
            continue
        lookup[user.github.lower()] = slack_id
    return lookup


def resolve_slack_id(github_login: str, lookup: dict[str, str]) -> tuple[str | None, str]:
    """Resolve a GitHub login to a Slack user ID.

    Returns a (slack_id, display) pair.  When the login is not in the lookup,
    slack_id is None and display is the raw GitHub login for text fallback.
    A warning is logged so missing mappings are visible in CI logs.

    Args:
        github_login: GitHub username to resolve.
        lookup: Pre-built lookup dict from :func:`build_lookup`.

    Returns:
        Tuple of (slack_id | None, display_string).
    """
    slack_id = lookup.get(github_login.lower())
    if slack_id is None:
        logger.warning(
            "No Slack mapping found for GitHub user %r — will use plain-text mention.",
            github_login,
        )
        return None, github_login
    return slack_id, f"<@{slack_id}>"
