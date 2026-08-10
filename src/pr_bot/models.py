"""Domain dataclasses shared across all modules."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class UserMapping:
    """Maps a GitHub login to a Slack username."""

    github: str
    slack_username: str


@dataclass(frozen=True)
class RemindersConfig:
    """Reminder behaviour flags loaded from config."""

    ignore_drafts: bool = True
    ping_author_if_no_reviewer: bool = True
    parse_body_mentions: bool = True
    stale_hours: int = 0


@dataclass(frozen=True)
class AppConfig:
    """Complete validated application configuration."""

    slack_channel: str
    repositories: list[str]
    reminders: RemindersConfig
    users: list[UserMapping]
    github_token: str
    slack_token: str


@dataclass(frozen=True)
class PullRequest:
    """Minimal PR representation fetched from GitHub."""

    number: int
    title: str
    url: str
    repo: str
    author_login: str
    reviewer_logins: list[str]
    body: str
    draft: bool
    created_at: datetime

    def age_hours(self, now: datetime) -> float:
        """Return PR age in hours relative to *now*."""
        return (now - self.created_at).total_seconds() / 3600


@dataclass
class ReminderTarget:
    """A Slack user to notify, together with the PRs they should review."""

    slack_id: str
    display: str  # used when slack_id is unknown (plain-text fallback)
    pull_requests: list[PullRequest] = field(default_factory=list)
