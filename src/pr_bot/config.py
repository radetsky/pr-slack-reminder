"""Configuration loading: YAML file + environment variable secrets."""

import os
import sys

import yaml

from pr_bot.models import AppConfig, RemindersConfig, UserMapping


def _require_env(name: str) -> str:
    """Return the value of an environment variable or exit with an error message.

    Args:
        name: Environment variable name.

    Returns:
        The variable's value.
    """
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: required environment variable {name!r} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def _parse_reminders(raw: dict) -> RemindersConfig:
    """Build a RemindersConfig from a raw YAML dict, applying defaults.

    Args:
        raw: Mapping parsed from the 'reminders' section of the YAML.

    Returns:
        Validated RemindersConfig instance.
    """
    return RemindersConfig(
        ignore_drafts=bool(raw.get("ignore_drafts", True)),
        ping_author_if_no_reviewer=bool(raw.get("ping_author_if_no_reviewer", True)),
        parse_body_mentions=bool(raw.get("parse_body_mentions", True)),
        stale_hours=int(raw.get("stale_hours", 0)),
    )


def _parse_users(raw: list[dict]) -> list[UserMapping]:
    """Build UserMapping list from the 'users' section of the YAML.

    Args:
        raw: List of {'github': ..., 'slack_username': ...} dicts.

    Returns:
        List of UserMapping instances.

    Raises:
        SystemExit: If any entry is missing 'github' or 'slack_username' keys.
    """
    mappings: list[UserMapping] = []
    for entry in raw:
        if "github" not in entry or "slack_username" not in entry:
            print(
                f"ERROR: user entry missing 'github' or 'slack_username' key: {entry}",
                file=sys.stderr,
            )
            sys.exit(1)
        mappings.append(
            UserMapping(github=str(entry["github"]), slack_username=str(entry["slack_username"]))
        )
    return mappings


def load_config(path: str, repo_overrides: list[str], channel_override: str | None) -> AppConfig:
    """Load and validate application configuration from *path*.

    Secrets (GH_PAT, SLACK_BOT_TOKEN) are read from environment variables,
    never from the YAML file.

    Args:
        path: Filesystem path to the YAML config file.
        repo_overrides: If non-empty, replaces the 'repositories' list from YAML.
        channel_override: If set, replaces the Slack channel from YAML.

    Returns:
        Fully populated AppConfig.

    Raises:
        SystemExit: On missing required fields or missing env secrets.
    """
    try:
        with open(path) as fh:
            raw = yaml.safe_load(fh) or {}
    except OSError as exc:
        print(f"ERROR: cannot open config file {path!r}: {exc}", file=sys.stderr)
        sys.exit(1)

    repositories: list[str] = repo_overrides if repo_overrides else raw.get("repositories", [])
    if not repositories:
        print("ERROR: no repositories configured (add to config or pass --repo).", file=sys.stderr)
        sys.exit(1)

    slack_section = raw.get("slack", {})
    channel = channel_override or slack_section.get("channel", "")
    if not channel:
        print("ERROR: Slack channel not configured (add slack.channel or pass --channel).", file=sys.stderr)
        sys.exit(1)

    return AppConfig(
        slack_channel=channel,
        repositories=repositories,
        reminders=_parse_reminders(raw.get("reminders", {})),
        users=_parse_users(raw.get("users", [])),
        github_token=_require_env("GH_PAT"),
        slack_token=_require_env("SLACK_BOT_TOKEN"),
    )
