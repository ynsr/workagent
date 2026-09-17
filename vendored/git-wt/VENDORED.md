# Vendored: git-wt

Source snapshot of [git-wt](https://github.com/ynsr/git-wt) (`feat` branch
development lives in `~/projects/personal/cli-agents-config/tools/git-wt`),
vendored here so `harness` installs/works standalone.

- **This directory is NOT a git repo** — exactly one `.git` exists, at the
  `harness` project root.
- Canonical development of git-wt happens in `cli-agents-config/tools/git-wt`;
  sync changes back there (or re-vendor with:
  `rsync -a --exclude .git --exclude build --exclude __pycache__ --exclude .venv <src>/ vendored/git-wt/`).
- Install git-wt from here: `pipx install ./vendored/git-wt` (or
  `cd vendored/git-wt && ./install.sh`).
