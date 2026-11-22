"""`orchestrate` command: conversational multi-worktree orchestrator mode."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import typer

from . import repos, store
from .cli_core import EXIT_USAGE, _catch_harness_errors, _fail, _print_result, app, eprint
from .cli_harness import _run_harness, append_extra_prompt


def build_orchestrator_prompt(
    user_prompt: str,
    repo_entries: list[tuple[str, str]],
) -> str:
    """Build the scoped coordinator prompt for `workagent orchestrate`."""
    lines = [
        "You are a Workagent multi-worktree orchestrator.",
        "Your job is to inspect the target repositories, decompose the goal below into",
        "independent tasks/issues/branches, and spawn parallel worker runs in their own worktrees.",
        "",
        "Available Workagent CLI tools (already on PATH):",
        "- `workagent status --json` — inspect active worktrees, branches, PRs, and CI",
        "- `workagent candidates --json` — inspect unlinked PRs/MRs and recent issues",
        "- `workagent message show <ref> --json` — inspect a registered Mattermost message/thread",
        "- `workagent start <ref> --repo <repo> --acp --yes` — spawn a worker in its own worktree",
        "- `workagent flow run <template> <ref> --acp --yes` — run a flow template on a worktree",
        "",
        "Environment note: `WORKAGENT_PARENT_RUN_ID` is set in your environment so every",
        "worker run you spawn via `workagent` is automatically linked back to this orchestrator run.",
    ]
    if repo_entries:
        lines.append("")
        lines.append("Target repositories:")
        for name, path in repo_entries:
            lines.append(f"- {name}: {path}")
    lines.extend([
        "",
        "Goal:",
        user_prompt.strip(),
    ])
    return "\n".join(lines)


def _resolve_target_repos(repo_args: Optional[list[str]]) -> list[tuple[str, str]]:
    """Resolve requested --repo values (or all registered repos if omitted)."""
    reg = store.load_config().get("repos", {})
    if not isinstance(reg, dict):
        reg = {}
    resolved: list[tuple[str, str]] = []
    if repo_args:
        for raw in repo_args:
            r = (raw or "").strip()
            if not r:
                continue
            path = repos.resolve_repo(r, Path.cwd())
            name = r
            for reg_name, reg_info in reg.items():
                if isinstance(reg_info, dict) and str(reg_info.get("path") or "") == str(path):
                    name = reg_name
                    break
            resolved.append((name, str(path)))
        return resolved
    for reg_name, reg_info in sorted(reg.items()):
        if isinstance(reg_info, dict) and reg_info.get("path"):
            resolved.append((reg_name, str(reg_info["path"])))
    return resolved


@app.command("orchestrate")
@_catch_harness_errors
def orchestrate(
    repo: Optional[list[str]] = typer.Option(
        None, "--repo", help="Target repo name/path (repeatable; defaults to all registered repos)."
    ),
    prompt: str = typer.Option("", "--prompt", help="Multi-part goal or pasted thread for the orchestrator."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run."),
    acp: bool = typer.Option(False, "--acp", help="Run via ACP stdio (default for orchestrate)."),
    launch: bool = typer.Option(False, "--launch", help="Run headless instead of ACP."),
    terminal: bool = typer.Option(False, "--terminal", help="Run in OS terminal instead of ACP."),
    run_mode: Optional[str] = typer.Option(
        None, "--run-mode", help="Run mode: preview|headless|acp|terminal (default: acp)."
    ),
    model: Optional[str] = typer.Option(None, "--model", help="Model id."),
    effort: Optional[str] = typer.Option(None, "--effort", help="Effort level."),
    extra_prompt: Optional[str] = typer.Option(None, "--extra-prompt", help="Extra instructions appended to the prompt."),
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Shell script sourced before commands."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without launching."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Launch a conversational multi-worktree orchestrator session."""
    if not (prompt or "").strip():
        _fail("--prompt is required and must not be blank", EXIT_USAGE)
    if sum(bool(x) for x in (launch, terminal, acp)) > 1:
        _fail("--launch, --terminal, and --acp are mutually exclusive", EXIT_USAGE)
    rm = (run_mode or "").strip().lower() if isinstance(run_mode, str) else ""
    if rm and rm not in ("preview", "headless", "acp", "terminal"):
        _fail(f"bad --run-mode {run_mode!r}: use preview, headless, acp, or terminal", EXIT_USAGE)
    mode = rm if rm else ("terminal" if terminal else ("headless" if launch else "acp"))
    use_launch = mode == "headless"
    use_acp = mode == "acp"
    use_terminal = mode == "terminal"

    repo_entries = _resolve_target_repos(repo)
    workdir = repo_entries[0][1] if repo_entries else os.getcwd()
    body = build_orchestrator_prompt(prompt, repo_entries)
    if (extra_prompt or "").strip():
        body = append_extra_prompt(body, extra_prompt.strip())

    harness_name = harness or store.default_harness()
    repo_names = [name for name, _ in repo_entries]
    if dry_run:
        _print_result(
            {
                "command": "orchestrate",
                "mode": mode,
                "harness": harness_name,
                "repos": repo_names,
                "workdir": workdir,
                "dry_run": True,
                "prompt_chars": len(body),
            },
            json_output,
        )
        return

    if not yes and sys.stdin.isatty():
        eprint(f"launch orchestrator ({mode}, harness={harness_name})? [y/N] ", end="")
        if (sys.stdin.readline() or "").strip().lower() not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)

    run_meta: dict = {"action": "orchestrate", "mode": mode, "repos": repo_names}
    run_result: dict = {
        "command": "orchestrate",
        "repos": repo_names,
        "workdir": workdir,
        "session_metadata": run_meta,
        "propagate_parent_run_id": True,
    }
    _run_harness(
        harness_name,
        body,
        workdir,
        workdir,
        True,
        use_launch,
        run_result,
        json_output,
        run_key="orchestrate",
        session_file=session_file if isinstance(session_file, str) else None,
        terminal=use_terminal,
        env_file=env_file,
        acp=use_acp,
        run_mode=mode,
        model=model,
        effort=effort,
    )
