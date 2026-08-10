# pr-bot

A one-shot CLI that scans open GitHub pull requests and pings the right people in Slack when a review is pending.

Run it however you like: cron, GitHub Actions, a manual invocation before standup. Each run fetches open PRs, works out who owes a review, and posts one digest message per Slack user. There's no daemon, no database, no state kept between runs.

## How it decides who to ping

For each open PR, in order:

1. Anyone in GitHub's `requested_reviewers` list. GitHub removes a person from this list once they submit a review, so this resolves itself as reviews come in.
2. If nobody is left and `ping_author_if_no_reviewer` is on, the PR author gets pinged instead.
3. If `parse_body_mentions` is on, any `@login` found in the PR body is added too.

A reviewer or mentioned user is skipped if their most recent review or comment is newer than the PR's latest commit. They've already had their say, and the bot won't nag them again until the author pushes new changes.

Everything gets merged: if the same person is on multiple PRs, or across multiple repos, they get one Slack message listing all of them, not one message per PR.

## Setup

```bash
pip install -e ".[dev]"
```

Copy `config.example.yaml` to `config.yaml` and fill in your repos, channel, and user mapping:

```yaml
slack:
  channel: "C0123456789"  # channel ID (preferred) or "#channel-name"

repositories:
  - owner/repo-a
  - owner/repo-b

reminders:
  ignore_drafts: true
  ping_author_if_no_reviewer: true
  parse_body_mentions: true
  stale_hours: 0  # 0 = no filter; >0 = only PRs open longer than N hours

users:
  - github: alice
    slack_username: alice.smith
  - github: bob
    slack_username: bob.jones
```

`users` maps a GitHub login to a Slack *username*, not a Slack ID. The bot resolves usernames to IDs against the workspace at runtime, so you don't have to go digging for member IDs by hand.

### Secrets

Two environment variables, never stored in YAML:

- `GH_PAT`: a GitHub personal access token with read access to the configured repos.
- `SLACK_BOT_TOKEN`: a Slack **Bot User OAuth Token** (`xoxb-...`) with the `chat:write` and `users:read` scopes. `users:read` is required even for `--dry-run`, since the bot resolves usernames to IDs on every run.

## Running it

```bash
python -m pr_bot --config config.yaml --dry-run --verbose
```

Drop `--dry-run` to actually post to Slack. Useful flags:

- `--repo owner/name`: scan this repo instead of the ones in config (repeatable).
- `--channel ID`: post to this channel instead of the one in config.
- `--verbose`: DEBUG-level logging.

## Scheduling

`.github/workflows/reminder.yml` runs the bot on weekdays at 09:00 and 14:00 UTC via GitHub Actions, and can also be triggered manually from the Actions tab (`workflow_dispatch`). It expects three repo secrets: `GH_PAT`, `SLACK_BOT_TOKEN`, and `CONFIG_YAML`.

`config.yaml` is never committed (see `.gitignore`), since it lists your repos, Slack channel, and the GitHub-to-Slack user mapping. In CI, the workflow writes it from the `CONFIG_YAML` secret before running the bot. To set it up:

1. In your repo, go to Settings -> Secrets and variables -> Actions -> New repository secret.
2. Name it `CONFIG_YAML`.
3. Paste the full contents of your local `config.yaml` as the value.

This keeps the config out of the repo entirely, which matters if the repo is public: your channel ID and team's Slack usernames stay private even though the bot's code doesn't.

## Testing

```bash
python -m pytest
```

## Gotchas

- A fine-grained `GH_PAT` scoped to an organization's repos needs an org admin to approve it (Organization Settings -> Personal access tokens -> Pending requests) before it works. Until then every call returns `401`.
- Using the Slack App-Level Token (`xapp-...`) instead of the Bot User OAuth Token will fail every API call with `not_allowed_token_type`. You want the one from OAuth & Permissions, not Basic Information.
- The reviewed-since-push check costs two extra GitHub API calls per non-draft, non-stale PR (fetching reviews and commits). Fine for normal team sizes, worth knowing if you're watching rate limits on a large repo set.
