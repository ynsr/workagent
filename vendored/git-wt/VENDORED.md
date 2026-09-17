# Vendored: git-wt

Source snapshot of git-wt, vendored from
`~/projects/personal/cli-agents-config/tools/git-wt` (upstream head at
`5da5bb6`) so `harness` installs/works standalone.

- **This directory is NOT a git repo** — exactly one `.git` exists, at the
  `harness` project root.
- Canonical development of git-wt happens in `cli-agents-config/tools/git-wt`;
  re-vendor with `./sync-git-wt.sh` (pass an alternate upstream path if it
  moves).
- Install git-wt from here: `pipx install ./vendored/git-wt` (install.sh
  does this automatically when git-wt is missing from PATH).
