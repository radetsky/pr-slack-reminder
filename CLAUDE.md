# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

The project venv lives at `.python-venv/` (not `.venv/`). Activate it or call its binaries directly.

```bash
# install dev deps (editable install)
.python-venv/bin/pip install -e ".[dev]"

# run all tests
.python-venv/bin/python -m pytest

# run a single test file or a single test case
.python-venv/bin/python -m pytest tests/test_config.py::test_load_valid_config

# dry-run the bot with a local config (requires GH_PAT + SLACK_BOT_TOKEN env vars)
.python-venv/bin/python -m pr_bot --config config.yaml --dry-run --verbose
```

Secrets (`GH_PAT`, `SLACK_BOT_TOKEN`) are read only from environment variables, never from YAML. Test fixtures set them via `monkeypatch.setenv`; `load_config()` calls `sys.exit(1)` if either is missing.

## Architecture

`pr_bot` is a one-shot CLI (`python -m pr_bot`), not a long-running service. Each run does: load config -> fetch open PRs from GitHub -> resolve reviewers/authors to Slack IDs -> merge into per-user digests -> post (or print) one Slack message per run.

Module responsibilities, in call order from `__main__.main()`:

- `config.py` — parses `config.yaml` and overlays env secrets and CLI overrides (`--repo`, `--channel`) into a frozen `AppConfig`. All validation failures here exit the process (`sys.exit(1)`) rather than raising, since this is a CLI, not a library.
- `slack_client.fetch_workspace_usernames()` — lists the whole Slack workspace via `users.list` (needs the `users:read` OAuth scope) and returns a lowercase-username -> Slack-ID dict. Called once per run, before target resolution, since config only stores usernames, not IDs. Indexes each member under *both* their legacy `name` and their `profile.display_name`, because the two can differ (e.g. `name: alexey.radetsky` vs `display_name: rad`) and either could be what someone put in `config.yaml`.
- `reminder.py` — the core orchestration. `build_reminder_targets(cfg, username_to_id)` fetches PRs per configured repo, applies the draft/stale-hours filters, computes `_reviewed_since_push()` per PR (an extra reviews + commits GitHub call, reusing the same reviews payload to also derive the PR's `ReviewStatus`), and calls `_collect_targets_for_pr()` per PR to decide who gets pinged, then merges results across repos into one `ReminderTarget` per Slack user (or per unmapped GitHub login).
- `github_client.py` — thin GitHub REST wrapper: pagination via `Link` headers, PR JSON -> `PullRequest` dataclass, `parse_body_mentions()` for `@login` extraction from PR bodies, and `fetch_pr_reviews()` / `fetch_pr_commits()` / `fetch_pr_issue_comments()` plus `latest_review_per_login()` / `latest_comment_per_login()` / `latest_commit_at()` for the "already responded since the last push" check, and `latest_decisive_review_per_login()` (ignores `COMMENTED` reviews) for the approved/needs-work status.
- `mapping.py` — GitHub login -> Slack ID lookup. `build_lookup(users, username_to_id)` resolves each config entry's `slack_username` against the workspace dict; a username not found there (typo, or the person left the workspace) is skipped with a warning rather than failing the run. `resolve_slack_id()` then looks up a GitHub login in that resolved dict, falling back to plain-text display for anyone still unmapped, so nobody is silently dropped.
- `slack_client.py` — also builds Block Kit blocks and sends via `slack_sdk.WebClient`, or prints the JSON payload when `--dry-run` is set.
- `models.py` — shared frozen dataclasses (`AppConfig`, `PullRequest`, `ReminderTarget`, etc.) used across all modules; this is the vocabulary the other modules share.

### Reminder target resolution order (`reminder._collect_targets_for_pr`)

1. PR's `requested_reviewers` (GitHub clears this list once a review is submitted, so it self-resolves).
2. If empty, or the PR already has a standing approval (see below), and `reminders.ping_author_if_no_reviewer` is true -> the PR author.
3. If `reminders.parse_body_mentions` is true -> any additional `@mentions` found in the PR body.

Duplicates across these three sources (and across repos) collapse by Slack ID, or by lowercased login when unmapped.

Logins from sources 1 and 3 are filtered through `reminder._should_ping(login, signal)`, given the `ReviewSignal` from `_reviewed_since_push` — see below for exactly what that means. The author fallback (source 2) is exempt from this filter.

### Who counts as satisfied (`reminder._reviewed_since_push`, `reminder.ReviewSignal`)

`_reviewed_since_push` returns a `ReviewSignal(satisfied, status, reopened)`. `_should_ping(login, signal)` decides per login: a login in `reopened` is always pinged; otherwise, once `status is ReviewStatus.APPROVED` **nobody else from sources 1/3 is pinged at all** — the PR is treated as reviewed regardless of whether some other requested reviewer never responded, and only the author (plus any `reopened` login) gets pinged; short of a standing approval, a login is pinged unless it's in `satisfied`.

How `satisfied`, `status`, and `reopened` are derived:

- **Approved: standing until reopened.** Once a login's latest decisive review (`github_client.latest_decisive_review_per_login`, which ignores plain `COMMENTED` reviews) is `APPROVED`, they are satisfied indefinitely — new commits do *not* bring them back, since GitHub itself never requires re-approval after a push, and this holds even if a *different* login is the PR's actual requested reviewer and never responded at all. The only way to re-ping them is an explicit `@login` mention in a Conversation-tab comment (`fetch_pr_issue_comments`) posted *after* their approval timestamp (`reminder._remmentioned_after`) — a deliberate callout, not just new code landing. Such logins land in `reopened`.
- **Not approved: satisfied only while fresh.** Anyone who hasn't approved (never reviewed, only left a plain comment, or requested changes) is satisfied only while their latest formal review or plain comment is at least as new as the PR's latest commit (`fetch_pr_commits`). A new commit makes them owed another look. Plain Conversation-tab comments count here too, alongside formal reviews, because GitHub only clears `requested_reviewers` for a formal review (Approve/Request changes/Comment via the Files-changed review flow) — a reviewer who only ever left a Conversation-tab comment would otherwise be re-pinged forever.

### PR review status emoji (`slack_client._pr_line`)

Each surviving PR is tagged with a `models.ReviewStatus` (`PENDING`, `APPROVED`, `NEEDS_WORK`), derived from the same `fetch_pr_reviews()` / `fetch_pr_issue_comments()` calls used above, so it costs no extra GitHub request:

- `APPROVED` — at least one login has a standing approval (per the rule above, not reopened by a later @-mention) and none of the decisive reviews is `CHANGES_REQUESTED`. This does not require the approval to come from an officially requested reviewer — any decisive `APPROVED`, even from a body-mentioned or drive-by reviewer, counts.
- `NEEDS_WORK` — not approved, `pr.reviewer_logins` is empty (nobody is still owed a first review), and at least one reviewer already responded since the latest commit — i.e. everyone weighed in but nobody approved.
- `PENDING` — everything else (still waiting on a first review).

`slack_client._pr_line()` prefixes the age-tier emoji with `:white_check_mark:` for `APPROVED` or `:pencil2:` for `NEEDS_WORK`; `PENDING` gets no prefix.

### Config shape

Top-level YAML keys: `slack` (channel), `repositories` (list of `owner/repo`), `reminders` (behavior flags: `ignore_drafts`, `ping_author_if_no_reviewer`, `parse_body_mentions`, `stale_hours`), `users` (GitHub login <-> Slack *username* mapping — resolved to a Slack ID at runtime, not stored as one). See `config.example.yaml` for the exact shape.

## Gotchas

- `--config` defaults to the literal `"config.yaml"` relative to the working directory — there is no repo-bundled fallback. Pass `--config` explicitly or run from a directory that has one.
- `--repo` and `--channel` CLI flags fully replace their YAML counterparts; they do not merge with the YAML lists.
- The scheduled workflow (`.github/workflows/reminder.yml`) runs weekdays at 09:00 and 14:00 UTC; use `workflow_dispatch` in the GitHub UI to trigger it manually for testing.
- The Slack bot token needs the `users:read` scope in addition to `chat:write` — `fetch_workspace_usernames()` runs on every invocation (including `--dry-run`) since target resolution depends on it, so a missing scope fails the run even in dry-run mode.
- The Slack token must be the **Bot User OAuth Token** (`xoxb-...`) from OAuth & Permissions, not the App-Level Token (`xapp-...`) from Basic Information — the latter is for Socket Mode only and fails every Web API call with `not_allowed_token_type`.
- A fine-grained `GH_PAT` scoped to an organization's repos does not work immediately after generation — an org admin/owner must approve it under Organization Settings -> Personal access tokens -> Pending requests. Until approved, every GitHub API call returns `401 Unauthorized`.
- Every non-draft, non-stale PR now costs three extra GitHub API calls (`fetch_pr_reviews`, `fetch_pr_commits`, `fetch_pr_issue_comments`) on top of the paginated PR list, for the reviewed-since-push check. Worth remembering if a large repo set ever runs into rate limits.
- `config.yaml` is gitignored and never committed. In CI, the workflow writes it from the `CONFIG_YAML` repo secret before running the bot, so a repo secret with that exact name must exist for the scheduled workflow to work at all.
