# pr-bot development notes for agents

## Prerequisites

- Python >= 3.11 (tested against 3.14)
- Secrets `GH_PAT` and `SLACK_BOT_TOKEN` must be in the environment; neither is loaded from YAML config. Test fixtures use `monkeypatch.setenv`. Local secrets were copied from `.env.fish`.
- The project venv lives at `.python-venv/` (not `.venv/`) — use its binaries directly, e.g. `.python-venv/bin/python`.

## Commands

```bash
# install dev deps (editable, pulls --extra-index-url extras)
.python-venv/bin/pip install -e ".[dev]"

# run all tests
.python-venv/bin/python -m pytest

# run a single test file or case
.python-venv/bin/python -m pytest tests/test_config.py::test_load_valid_config

# dry-run the bot with your local config (requires GH_PAT + SLACK_BOT_TOKEN env vars)
.python-venv/bin/python -m pr_bot --config config.yaml --dry-run --verbose
```

## Architecture in one paragraph

`pr_bot` scans configured GitHub repos for open PRs awaiting review, resolves each PR author/reviewer's GitHub login to a Slack username via the `users` mapping in YAML, then resolves that username to a Slack user ID by listing the workspace via `users.list` (needs the `users:read` OAuth scope), and sends a digest message to a Slack channel. Secrets flow exclusively through env vars; the YAML file only contains targets, mappings, and reminder toggles.

## Gotchas worth remembering

- `config.yaml` path defaults to the working-directory-relative literal `"config.yaml"`, not to any repo-bundled default. Pass `--config` explicitly or keep one at CWD root.
- `--repo` and `--channel` CLI flags fully override their YAML counterparts; they do not merge.
- Tests that call `load_config()` must set `GH_PAT` and `SLACK_BOT_TOKEN` on the monkeypatch — omitting either causes `SystemExit`.
- Bot runs on a schedule (weekdays 09:00 and 14:00 UTC) via `.github/workflows/reminder.yml`; use `workflow_dispatch` to test manually in CI.
- Configured repositories, PR authors/slack users, and reminders sections all live under top-level keys in YAML (`slack`, `repositories`, `reminders`, `users`).
- `users` entries map `github` to `slack_username` (not a Slack ID); `slack_client.fetch_workspace_usernames()` resolves usernames to IDs at runtime, so a typo'd or departed username is skipped with a warning rather than failing the run.
- Slack members have two different "usernames" — the legacy `name` field and `profile.display_name` — and they can differ (e.g. `alexey.radetsky` vs `rad`). `fetch_workspace_usernames()` indexes both, so a `slack_username` in config can be either one.
- `SLACK_BOT_TOKEN` must be the Bot User OAuth Token (`xoxb-...`), not the App-Level Token (`xapp-...`); the latter fails every Web API call with `not_allowed_token_type`.
- A fine-grained `GH_PAT` scoped to an org's repos needs an org admin to approve it (Organization Settings -> Personal access tokens -> Pending requests) before it works; until then every GitHub call 401s.
