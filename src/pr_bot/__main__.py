"""CLI entry point: python -m pr_bot [options]."""

import argparse
import logging
import sys

from pr_bot.config import load_config
from pr_bot.reminder import build_reminder_targets
from pr_bot.slack_client import fetch_workspace_usernames, send_or_print


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:] when None).

    Returns:
        Parsed Namespace with config, repo, channel, dry_run, verbose fields.
    """
    parser = argparse.ArgumentParser(
        prog="pr-bot",
        description="Remind Slack users about pending GitHub PR reviews.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        metavar="PATH",
        help="Path to YAML config file (default: config.yaml).",
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="OWNER/NAME",
        help="Repository to scan (repeatable; overrides config repositories).",
    )
    parser.add_argument(
        "--channel",
        default=None,
        metavar="ID",
        help="Slack channel ID or name (overrides config slack.channel).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the Slack payload to stdout instead of sending.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the PR review reminder bot.

    Loads config, fetches open PRs, resolves Slack targets, and sends (or
    prints) the digest message.

    Args:
        argv: Optional argument list for testing; defaults to sys.argv[1:].
    """
    args = _parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )

    cfg = load_config(
        path=args.config,
        repo_overrides=args.repo,
        channel_override=args.channel,
    )

    username_to_id = fetch_workspace_usernames(cfg.slack_token)
    targets = build_reminder_targets(cfg, username_to_id)

    send_or_print(
        targets=targets,
        channel=cfg.slack_channel,
        slack_token=cfg.slack_token,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
    sys.exit(0)
