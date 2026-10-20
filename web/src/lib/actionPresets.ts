export interface ActionPreset {
  id: string
  name: string
  icon: string
  prompt: string
  pre_execution: string
  post_execution: string
  run_mode: string
}

const OPEN_MR_PROMPT = `Open a GitLab merge request from the current branch to the repo's default branch, unless one already exists.

Steps:
1. Resolve the default/base branch at runtime: read \`git symbolic-ref refs/remotes/origin/HEAD\` (e.g. \`refs/remotes/origin/main\` -> \`main\`). If it cannot be resolved, fall back to \`develop\` if it exists remotely, else \`main\`. Determine the current branch (\`git rev-parse --abbrev-ref HEAD\`). If it is the base branch, stop and tell me.
2. Run \`git fetch origin\`. Check whether the branch is pushed and up to date with its remote: \`git rev-parse --abbrev-ref @{u}\` fails when there is no upstream; \`git status -sb\` shows ahead/behind counts. The branch must be pushed fast-forward only (never \`--force\`) before opening the MR. In preview mode (WORKAGENT_PREVIEW=1) do not push — only report how many commits would be pushed.
3. Re-check with \`glab\` for an existing OPEN MR with this source branch and the resolved base branch as target. Re-check because the pre-execution gate may have raced (the MR could have appeared after the gate ran).
   - If one exists: don't create a duplicate. Report its URL, state (open/draft), title, and whether the local branch is ahead of or behind it. Don't modify it unless I ask.
4. If none exists:
   - Check there are commits ahead of \`origin/<base>\` (\`git log origin/<base>..HEAD\`). If none, stop and tell me.
   - Review \`git diff origin/<base>...HEAD\` (three-dot) and the commit messages. If the diff is very large, use \`--stat\` and inspect only the key files.
   - Create the MR with:
     - Title: concise, imperative, in the same language as the commits, and consistent with the commit/branch naming (include the ticket ID if it appears in the branch name or commits).
     - Description with sections: **Summary** (what and why), **Changes** (bullet list grouped by module/area), **Testing** (only what's evidenced by the diff or what I told you; don't invent), **Notes** (migrations, config changes, breaking changes, or risks, if any).
     - Source: current branch. Target: the resolved base branch.
5. Output the MR URL and the generated title and description.

Rules: don't merge, rebase, or force-push; don't touch other MRs; list dirty (uncommitted) files and warn me if there are any, but review only the committed diff (\`git diff origin/<base>...HEAD\`) and never \`git add\` anything.
`

const OPEN_MR_PRE_EXECUTION = `br=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 1
base=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|^refs/remotes/origin/||')
if [ -z "$base" ]; then
  if git show-ref --verify --quiet refs/remotes/origin/develop; then base=develop; else base=main; fi
fi
if [ "$br" = "$base" ]; then echo "SKIP: on base branch $base" >&2; exit 10; fi
git fetch origin >/dev/null 2>&1
ahead=$(git rev-list --count "origin/$base..HEAD" 2>/dev/null) || exit 1
if [ "$ahead" -eq 0 ]; then echo "SKIP: no commits ahead of origin/$base" >&2; exit 10; fi
if glab mr list --source-branch "$br" --target-branch "$base" --state opened 2>/dev/null | grep -q .; then echo "SKIP: open MR already exists for $br -> $base" >&2; exit 10; fi
if [ "$WORKAGENT_PREVIEW" = "1" ]; then echo "SKIP: would push $ahead commits ($br -> origin/$br)" >&2; exit 10; fi
git push origin HEAD || exit 1`

export const ACTION_PRESETS: ActionPreset[] = [
  {
    id: "open-mr",
    name: "Open MR",
    icon: "GitMerge",
    prompt: OPEN_MR_PROMPT,
    pre_execution: OPEN_MR_PRE_EXECUTION,
    post_execution: "",
    run_mode: "headless",
  },
]
