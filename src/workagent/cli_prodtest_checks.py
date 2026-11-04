"""Shared prodtest run pipeline: binding, validation, harness, persistence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from . import store, worktrees
from .cli_core import _fail, eprint
from .cli_harness import _run_harness, append_extra_prompt
from .errors import HarnessError

def _plan_stored_divergence(item: dict | None, plan_file: Path | None) -> str | None:
    """None = stored row matches disk; else a human-readable mismatch.

    Compares the stored ``plan_revision``/``plan_path`` row against the
    working-tree plan: catches a diverged store row (e.g. re-plan landed on
    disk but the upsert failed) before the harness runs. Never raises.
    """
    if not item:
        return None
    if plan_file is None or not str(plan_file):
        return None
    try:
        stored_path = str(item.get("plan_path", "") or "")
        if stored_path and Path(stored_path) != Path(str(plan_file)):
            return f"stored plan_path {stored_path} != run plan {plan_file}"
        stored_rev = item.get("plan_revision")
        if stored_rev not in (None, ""):
            import yaml as _yaml
            try:
                disk = _yaml.safe_load(Path(str(plan_file)).read_text(encoding="utf-8")) or {}
            except Exception:
                disk = None
            if isinstance(disk, dict) and "revision" in disk:
                try:
                    if int(disk.get("revision")) != int(stored_rev):
                        return (f"stored plan_revision {stored_rev} != "
                                f"on-disk revision {disk.get('revision')}")
                except (TypeError, ValueError):
                    pass
    except Exception:
        return None
    return None


def _run_locked(store_key: str, entry: dict, plan_file: Path | None, release_sha: str,
                issue_url: str, fix_mode: str, skip_version_gate: bool,
                out_dir: Path, timeout_s: int,
                launch: bool, terminal: bool, no_tty: bool, json_output: bool,
                harness: Optional[str], env_file: str | None,
                extra_prompt: Optional[str], session_file: str | None,
                item: dict | None = None) -> None:
    """Run body inside the per-issue lock: checkout, validate, harness, persist."""
    from . import release_worktree as _wt
    from . import prodtest_scripts as _ps
    from .release_util import worktree_base

    repo = str(entry.get("repo", "") or "")
    checkout: Path | None = None
    if repo and Path(repo).is_dir():
        dest = Path(str(worktree_base())) / f"prodtest-{store_key.replace('/', '-').replace(':', '-')}"
        try:
            checkout = _wt.prepare(repo, dest, release_sha)
        except HarnessError as e:
            _fail(f"checkout at {release_sha} failed: {e}", 1)
    else:
        eprint(f"warning: no repo checkout for {store_key}; skipping ephemeral checkout")

    _enforce_plan_release_binding(store_key, item=item, plan_file=plan_file, release_sha=release_sha, repo=repo, checkout=checkout)
    # Stored-row divergence is advisory (the SHA byte comparison is the gate):
    # warn loudly so a diverged store row is visible before the harness runs.
    divergence = _plan_stored_divergence(item, plan_file)
    if divergence:
        eprint(f"WARNING: stored plan metadata diverged for {store_key}: {divergence}")
    _run_validated(store_key, entry, plan_file, release_sha, issue_url,
                    fix_mode, skip_version_gate, out_dir, timeout_s,
                    launch, terminal, no_tty, json_output,
                    harness, env_file, extra_prompt, session_file,
                    repo, checkout)



def _rel_plan_candidates(plan_file: Path) -> list[str]:
    """Repo-relative candidate path for a plan file.

    Production layout stores the plan worktree-local at
    ``.workagent/prodtest/plans/<KEY>.yaml`` (committed in the MR branch but
    not necessarily under the release checkout root). Derive the canonical
    repo-relative path from the filename so ``git show
    <release_sha>:<path>`` can resolve the plan bytes at release time.
    """
    name = Path(str(plan_file)).name
    if not name:
        return []
    return [f".workagent/prodtest/plans/{name}"]


def _plan_manifest_allows_warn(checkout: Path | None, repo: str) -> bool:
    """True when the manifest fix_policy.plan_binding selects warn mode."""
    try:
        import yaml as _yaml
        base = checkout if checkout is not None and checkout.is_dir() else (Path(repo) if repo else None)
        man = (base / ".workagent" / "prodtest.yaml") if base is not None else None
        if man is not None and man.exists():
            policy = (_yaml.safe_load(man.read_text(encoding="utf-8")) or {}).get("fix_policy") or {}
            if isinstance(policy, dict) and str(policy.get("plan_binding", "") or "").lower() == "warn":
                return True
    except Exception:
        pass
    return False


def _plan_release_binding(store_key: str, plan_file: Path | None, release_sha: str,
                          repo: str, checkout: Path | None) -> str | None:
    """Plan-at-release-SHA binding: None = pass, 'blocked:…' / 'warn:…' otherwise.

    Compares the plan bytes at the release SHA (via the ephemeral checkout,
    falling back to ``repo``) against the working-tree plan: a mismatch means
    the stored plan is not what was reviewed at release time. Worktree-local
    plans (the production layout) are resolved to their canonical
    repo-relative ``.workagent/prodtest/plans/<KEY>.yaml`` path and read via
    ``git show <release_sha>:<path>``. Never raises.
    """
    if plan_file is None or not str(plan_file):
        return "warn:missing-plan"
    try:
        current = Path(str(plan_file)).read_bytes()
    except OSError:
        return "warn:missing-plan"
    base: Path | None = checkout if checkout is not None and checkout.is_dir() else None
    if base is None and repo and Path(repo).is_dir():
        base = Path(repo)
    if base is None:
        return None  # no checkout and no repo: per-skill preflight cannot run.
    rel: Path | None = None
    try:
        rel = Path(str(plan_file)).resolve().relative_to(Path(str(base)).resolve())
    except Exception:
        rel = None
    rels: list[str] = [rel.as_posix()] if rel is not None else []
    if not rels:
        # Worktree-local plan (production layout): resolve via the canonical
        # repo-relative plan path so the gate actually fires on mismatch.
        rels = _rel_plan_candidates(Path(str(plan_file)))
    if not rels:
        return "warn:plan-unverifiable-at-release"
    at_release: str | None = None
    try:
        from .errors import run_cmd as _run
        for cand in rels:
            try:
                at_release = _run("git", "-C", str(base), "show", f"{release_sha}:{cand}",
                                  echo=False)
                break
            except Exception:
                continue
    except Exception:
        return "warn:plan-unverifiable-at-release"
    if at_release is None:
        # Cannot prove the plan existed at the release SHA: block by default
        # so the gate fires; warn only when the manifest opts into warn mode.
        if _plan_manifest_allows_warn(checkout, repo):
            return "warn:plan-unverifiable-at-release"
        return "blocked:plan-unverifiable-at-release"
    # run_cmd returns stripped stdout while the working-tree plan is raw bytes,
    # so normalize symmetrically: identical newline-terminated plans must pass.
    try:
        current_text = current.decode("utf-8")
    except UnicodeDecodeError:
        current_text = current.decode("utf-8", errors="replace")
    if (at_release or "").strip() != current_text.strip():
        return "blocked:plan-changed-since-release"
    return None


def _enforce_plan_release_binding(store_key: str, item: dict | None, plan_file: Path | None,
                                  release_sha: str, repo: str, checkout: Path | None) -> None:
    """Fail or warn loudly on plan/release-SHA mismatch (manifest policy).

    ``manifest.fix_policy.plan_binding`` selects the mode: ``"fail"``
    (default) aborts the run, ``"warn"`` prints a loud warning and proceeds.
    Missing plans only warn — validation below owns that path.
    """
    binding = _plan_release_binding(store_key, plan_file, release_sha, repo, checkout)
    if binding is None:
        return
    mode = "fail"
    try:
        import yaml as _yaml
        base = checkout if checkout is not None and checkout.is_dir() else (Path(repo) if repo else None)
        man = (base / ".workagent" / "prodtest.yaml") if base is not None else None
        if man is not None and man.exists():
            policy = (_yaml.safe_load(man.read_text(encoding="utf-8")) or {}).get("fix_policy") or {}
            if isinstance(policy, dict) and str(policy.get("plan_binding", "") or "").lower() == "warn":
                mode = "warn"
    except Exception:
        mode = "fail"
    msg = (f"plan/release binding {binding} for {store_key} at {release_sha}: "
           "the working-tree plan differs from the plan at the release SHA "
           "(re-run `prodtest plan`, re-review, and release again)")
    if binding.startswith("blocked:") and mode == "fail":
        _fail(msg, 1)
    eprint(f"WARNING: {msg}")


def _run_validated(store_key: str, entry: dict, plan_file: Path | None, release_sha: str,
                   issue_url: str, fix_mode: str, skip_version_gate: bool,
                   out_dir: Path, timeout_s: int,
                   launch: bool, terminal: bool, no_tty: bool, json_output: bool,
                   harness: Optional[str], env_file: str | None,
                   extra_prompt: Optional[str], session_file: str | None,
                   repo: str, checkout: Path | None, item: dict | None = None) -> None:
    """Validated run body: plan validation, harness dispatch, persistence.

    Checkout lifecycle belongs to the caller (``_run_locked``): this function
    never finishes the checkout itself.
    """
    from . import prodtest_scripts as _ps
    manifest = (Path(repo) / ".workagent" / "prodtest.yaml") if repo else None
    if plan_file is not None and plan_file.exists():
        try:
            validation = _ps.run_validate(
                str(plan_file), str(manifest) if manifest and manifest.exists() else None)
        except Exception as e:
            _fail(f"plan validation failed: {e}", 1)
        if not validation.get("valid"):
            for err in validation.get("errors", []):
                eprint(f"plan invalid: {err}")
            _fail(f"plan {plan_file} invalid; fix the plan first", 1)
    else:
        eprint(f"warning: plan file {plan_file} missing; running without validation")

    run_request = {
        "checkout": str(checkout) if checkout else (repo or ""),
        "release_sha": release_sha,
        "plan_file": str(plan_file) if plan_file else "",
        "issue": {"key": store_key, "url": issue_url or None},
        "run_dir": str(out_dir),
        "fix_mode": fix_mode,
        "notifications_handled_by_harness": False,
        "skip_version_gate": skip_version_gate,
    }
    prompt = ("Use the prodtest-run skill with run request: "
              + json.dumps(run_request, indent=2))
    prompt = append_extra_prompt(prompt, extra_prompt)
    harness_name = harness or store.load_config().get("default_harness", "omp")
    worktree = str(entry.get("worktree", "") or repo or ".")
    branch = str(entry.get("branch", "") or "")
    result: dict = {"worktree_path": worktree, "branch": branch,
                    "command": "prodtest run", "key": store_key,
                    "run_dir": str(out_dir), "release_sha": release_sha,
                    "fix_mode": fix_mode}
    eprint(f"run dir: {out_dir}  release: {release_sha}")
    from .errors import HarnessTimeout as _HarnessTimeout
    try:
        _run_harness_guarded(harness_name, prompt, worktree, repo or worktree,
                             no_tty, launch, result, json_output, terminal,
                             env_file, session_file, timeout_s,
                             store_key if entry else None)
    except _HarnessTimeout as e:
        result["timeout"] = True
        if launch and no_tty:
            _finish_run(store_key, entry, plan_file, release_sha, issue_url,
                        fix_mode, out_dir, timeout_s, result, timed_out=True)
        _fail(f"prodtest run timed out after {timeout_s}s (budget + gate + slack); "
              f"marked BLOCKED", 1)
    if launch and no_tty:
        _finish_run(store_key, entry, plan_file, release_sha, issue_url,
                    fix_mode, out_dir, timeout_s, result)


def _run_harness_guarded(harness_name: str, prompt: str, worktree: str, fallback_dir: str,
                         no_tty: bool, launch: bool, result: dict, json_output: bool,
                         terminal: bool, env_file: str | None, session_file: str | None,
                         timeout_s: int, run_key: str | None) -> None:
    """Headless harness call with the run timeout; overrun kills the group.

    The harness child runs in its own process group (``start_new_session``,
    mirroring ``release_gates._run_group``); on overrun the group gets
    SIGTERM then SIGKILL synchronously, so no orphan survives. The lock and
    the ephemeral checkout stay held until the kill completes — the caller
    persists BLOCKED only after this raises.
    """
    if not (launch and no_tty):
        _run_harness(harness_name, prompt, worktree, fallback_dir,
                     no_tty, launch, result, json_output,
                     run_key=run_key, session_file=session_file,
                     terminal=terminal, env_file=env_file)
        return
    _run_harness(harness_name, prompt, worktree, fallback_dir,
                 no_tty, launch, result, json_output,
                 run_key=run_key, session_file=session_file,
                 terminal=terminal, env_file=env_file,
                 timeout_s=timeout_s)


_REPORT_POST_CAP = 20000


def _cap_report(text: str, cap: int = _REPORT_POST_CAP) -> str:
    """Bound the issue-tracker comment body; truncation is explicit."""
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n\n…(truncated: {len(text)} chars, cap {cap})"


def _validate_results_for_post(results_file: str, plan_file: Path | None, _ps) -> str:
    """Render the harness results after validating subjects against the plan.

    A misbehaving/compromised harness could stuff unexpected subjects into
    ``results.yaml``; warn loudly when a reported case id is not in the plan
    so the posted comment flags the mismatch instead of silently vouching.
    Raises on render failure (caller warns); never raises on validation.
    """
    import yaml as _yaml
    plan_ids: set[str] | None = None
    try:
        if plan_file is not None and Path(str(plan_file)).exists():
            plan = _yaml.safe_load(Path(str(plan_file)).read_text(encoding="utf-8")) or {}
            cases = plan.get("cases") if isinstance(plan, dict) else None
            if isinstance(cases, list):
                plan_ids = {str(c.get("id", "")) for c in cases if isinstance(c, dict) and c.get("id")}
    except Exception:
        plan_ids = None
    report = _ps.render(results_file)
    if plan_ids is not None:
        try:
            results = _yaml.safe_load(Path(results_file).read_text(encoding="utf-8")) or {}
            reported = results.get("cases") if isinstance(results, dict) else None
            if isinstance(reported, list):
                unknown = sorted({str(c.get("id", "")) for c in reported
                                  if isinstance(c, dict) and c.get("id")
                                  and str(c.get("id")) not in plan_ids})
                if unknown:
                    eprint(f"WARNING: results.yaml reports cases not in the plan: "
                           f"{', '.join(unknown)}")
                    report += ("\n\nWARNING: results report cases not covered by the plan: "
                               + ", ".join(unknown))
        except Exception as e:
            eprint(f"warning: results subject validation failed: {e}")
    return report


def _finish_run(store_key: str, entry: dict, plan_file: Path | None, release_sha: str,
                issue_url: str, fix_mode: str, out_dir: Path, timeout_s: int,
                result: dict, timed_out: bool = False) -> None:
    """Best-effort post-harness: parse result, render/redact/persist, comment, status.

    On *timed_out* (harness group already killed) the run is BLOCKED with a
    redacted partial artifact — never left unpersisted.
    """
    from . import harness_summary as _hs
    from . import issue_comments as _ic
    from . import prodtest_result as _pr
    from . import prodtest_scripts as _ps

    session_file = str(result.get("session_file", "") or "")
    stdout_text = ""
    if session_file:
        try:
            stdout_text = _hs.last_assistant_text(session_file)
        except Exception as e:
            eprint(f"warning: summary parse failed: {e}")
    payload: dict = {}
    try:
        payload = _pr.parse_result_line(stdout_text, str(out_dir), "PRODTEST_RUN")
    except Exception as e:
        eprint(f"warning: no PRODTEST_RUN_RESULT found ({e})")
    overall = str(payload.get("overall", "") or "").upper()
    status = {"PASS": "passed", "FAIL": "failed", "BLOCKED": "blocked"}.get(overall, "blocked")
    if timed_out:
        status = "blocked"
        overall = overall or "BLOCKED"
        payload = dict(payload) if isinstance(payload, dict) else {}
        payload.setdefault("overall", "BLOCKED")
        payload.setdefault("notes", f"timed out after {timeout_s}s; harness killed")
    run_id = str(payload.get("run_id", "") or result.get("session_id", "") or "")

    report = ""
    results_file = str(out_dir / "results.yaml")
    report_file = str(out_dir / "report.md")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        report = _validate_results_for_post(results_file, plan_file, _ps)
        Path(report_file).write_text(report, encoding="utf-8")
    except Exception as e:
        eprint(f"warning: report render failed: {e}")

    try:
        from . import prodtest_redact as _red
        redacted = _red.redact(report) if report else ""
        if redacted and issue_url:
            _ic.post_issue_comment(issue_url, _cap_report(redacted))
    except Exception as e:
        eprint(f"warning: report comment post failed: {e}")

    try:
        store.upsert_prodtest(store_key, status=status,
                              last_run_id=run_id or report_file)
    except Exception as e:
        eprint(f"warning: prodtest state upsert failed: {e}")

    fix = payload.get("fix") or {}
    _create_fix_followup(store_key, entry, issue_url, fix_mode, fix,
                         overall, out_dir, report_file, release_sha)


def _create_fix_followup(store_key: str, entry: dict, issue_url: str, fix_mode: str,
                         fix: dict, overall: str, out_dir: Path,
                         report_file: str, release_sha: str) -> None:
    """File a follow-up issue for FAIL/BLOCKED runs (best effort, never raises).

    Skill `prodtest-run` §8 assigns follow-up to the harness, but a harness
    that only reports `fix.decision` without an `issue_url` leaves the loop
    open. When ``fix_mode`` is ``issue``/``issue+mr`` and the verdict is not
    PASS, create the linked follow-up issue here (jira/gh/glab by the source
    issue URL) instead of merely logging. ``report-only`` keeps the old
    log-only behavior; existing ``fix.issue_url`` values are never duplicated.
    """
    if not isinstance(fix, dict) or fix.get("decision") in (None, "", "none"):
        return
    decision = str(fix.get("decision", "") or "")
    existing_url = str(fix.get("issue_url", "") or "")
    if fix_mode == "report-only":
        eprint(f"fix follow-up per manifest fix_policy: {decision} "
               f"(mode {fix_mode})")
        return
    if existing_url:
        eprint(f"fix follow-up per manifest fix_policy: {decision} "
               f"(mode {fix_mode}): {existing_url}")
        return
    verdict = str(overall or "").upper()
    if verdict == "PASS":
        eprint(f"fix follow-up per manifest fix_policy: {decision} "
               f"(mode {fix_mode})")
        return
    summary = (f"[prodtest] {store_key}: {verdict} at "
               f"{str(release_sha)[:12]} needs triage")
    body_lines = [summary, "",
                  f"Prodtest run for {store_key} finished {verdict} "
                  f"(release {release_sha}).",
                  f"Report: {report_file}",
                  f"Run dir: {out_dir}"]
    if decision:
        body_lines.append(f"Harness fix decision: {decision}")
    body_lines += ["", f"Original issue: {issue_url or store_key}"]
    body = "\n".join(body_lines)
    try:
        from . import refs as _refs
        from .errors import run_cmd as _run, HarnessError as _HE
        target = (issue_url or "").strip()
        try:
            parsed = _refs.parse_ref(target) if target else None
        except _HE:
            parsed = None
        tool = (parsed or {}).get("tool", "") if parsed else ""
        number = str((parsed or {}).get("number", "") or "") if parsed else ""
        if tool == "jira" and number:
            out = _run("jira", "issue", "create", "--no-input",
                       "-s", summary, "-b", body, echo=False)
            eprint(f"filed follow-up issue for {store_key} ({(out or '').strip()})")
        elif tool == "gh":
            repo = str((parsed or {}).get("repo", "") or "")
            args = ["gh", "issue", "create", "--title", summary, "--body", body]
            if repo:
                args += ["--repo", repo]
            out = _run(*args, echo=False)
            eprint(f"filed follow-up issue for {store_key} ({(out or '').strip()})")
        elif tool == "glab":
            repo = str((parsed or {}).get("repo", "") or "")
            # glab resolves the project from cwd; without a repo path or a
            # numeric project id the create call has nowhere to land.
            if repo and not repo[0].isdigit():
                eprint(f"warning: cannot file follow-up issue for {store_key}: "
                       "glab needs a project path/id context (harness owns follow-up here)")
            else:
                out = _run("glab", "issue", "create", "--title", summary,
                           "--description", body, echo=False)
                eprint(f"filed follow-up issue for {store_key} ({(out or '').strip()})")
        else:
            eprint(f"warning: cannot file follow-up issue for {store_key}: "
                   f"unparseable issue ref {target or store_key!r} "
                   f"(mode {fix_mode})")
    except Exception as e:
        eprint(f"warning: follow-up issue create failed for {store_key}: {e}")
