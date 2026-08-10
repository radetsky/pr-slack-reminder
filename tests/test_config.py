"""Tests for config loading and validation."""

import os
import textwrap

import pytest

from pr_bot.config import load_config


@pytest.fixture()
def config_file(tmp_path):
    """Write a minimal valid config.yaml and return its path."""
    content = textwrap.dedent("""\
        slack:
          channel: "C123"
        repositories:
          - owner/repo
        users:
          - github: alice
            slack_username: alice.smith
    """)
    path = tmp_path / "config.yaml"
    path.write_text(content)
    return str(path)


def test_load_valid_config(config_file, monkeypatch):
    """Happy path: all required fields present, secrets in env."""
    monkeypatch.setenv("GH_PAT", "ghp_test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    cfg = load_config(config_file, repo_overrides=[], channel_override=None)

    assert cfg.slack_channel == "C123"
    assert cfg.repositories == ["owner/repo"]
    assert len(cfg.users) == 1
    assert cfg.users[0].github == "alice"
    assert cfg.users[0].slack_username == "alice.smith"
    assert cfg.github_token == "ghp_test"
    assert cfg.slack_token == "xoxb-test"


def test_repo_override_replaces_config(config_file, monkeypatch):
    """--repo overrides the config list."""
    monkeypatch.setenv("GH_PAT", "ghp_test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    cfg = load_config(config_file, repo_overrides=["other/repo"], channel_override=None)
    assert cfg.repositories == ["other/repo"]


def test_channel_override(config_file, monkeypatch):
    """--channel overrides config channel."""
    monkeypatch.setenv("GH_PAT", "ghp_test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    cfg = load_config(config_file, repo_overrides=[], channel_override="C999")
    assert cfg.slack_channel == "C999"


def test_missing_gh_pat_exits(config_file, monkeypatch):
    """Missing GH_PAT env variable causes SystemExit."""
    monkeypatch.delenv("GH_PAT", raising=False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    with pytest.raises(SystemExit):
        load_config(config_file, repo_overrides=[], channel_override=None)


def test_missing_slack_token_exits(config_file, monkeypatch):
    """Missing SLACK_BOT_TOKEN env variable causes SystemExit."""
    monkeypatch.setenv("GH_PAT", "ghp_test")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    with pytest.raises(SystemExit):
        load_config(config_file, repo_overrides=[], channel_override=None)


def test_missing_repositories_exits(tmp_path, monkeypatch):
    """Config without repositories and no --repo override causes SystemExit."""
    monkeypatch.setenv("GH_PAT", "ghp_test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    path = tmp_path / "cfg.yaml"
    path.write_text("slack:\n  channel: C1\nusers: []\n")

    with pytest.raises(SystemExit):
        load_config(str(path), repo_overrides=[], channel_override=None)


def test_stale_hours_defaults_to_zero(config_file, monkeypatch):
    """stale_hours defaults to 0 when not in config."""
    monkeypatch.setenv("GH_PAT", "ghp_test")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    cfg = load_config(config_file, repo_overrides=[], channel_override=None)
    assert cfg.reminders.stale_hours == 0
