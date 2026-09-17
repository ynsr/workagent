# Changelog

## Unreleased

- Jira support: `KEY-123` / `<host>/browse/KEY-123` refs via `jira-cli`.
- Resilient `start`: existing branch/worktree resumes instead of failing;
  upstream verified to point at the feature branch, never the base.
- `start`/`review` accept extra harness args after `--`; `doctor` checks `jira-cli`.

## 0.1.0 — 2026-09-17

- Initial release: `start`, `review`, `cleanup`, `repo add/list/remove`,
  `status`, `doctor`, `completion`/`completion-install`.
- GitHub (`gh`) + GitLab (`glab`) issue/PR fetching; `omp` harness (v1 only).
- State in `~/.config/harness/`; `git-wt` via subprocess.
