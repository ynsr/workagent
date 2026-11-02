#!/usr/bin/env python3
"""Render results.yaml into the markdown report posted as the issue comment.
Usage: render_report.py results.yaml > report.md   (output is redacted)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml
from workagent.prodtest_redact import redact

ICON = {"PASS": "✅", "FAIL": "❌", "BLOCKED": "⛔", "SKIPPED": "⏭️"}


def cell(s, n=140):
    s = str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    r = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
    o = []
    overall = r.get("overall", "UNKNOWN")
    o.append(f"<!-- workagent:prodtest-results v1 run={r.get('run_id')} -->")
    o.append(f"## Production test {ICON.get(overall, '')} **{overall}** — {r.get('issue')}")
    o.append(f"Run `{r.get('run_id')}` · release `{r.get('release_sha')}` · plan rev {r.get('plan_revision')} · {r.get('started_at')} → {r.get('finished_at')}")
    vg = r.get("version_gate") or {}
    o.append(f"\n**Version gate:** {vg.get('status', 'n/a')} (observed `{cell(vg.get('observed'), 60)}`, waited {vg.get('waited_s', 0)}s)")
    pre = r.get("preconditions") or []
    if pre:
        o.append("\n**Preconditions:** " + ", ".join(f"{p['id']} {ICON.get(p['verdict'], p['verdict'])}" for p in pre))
    cases = r.get("cases") or []
    o.append("\n| Case | Title | Verdict | Notes |\n|---|---|---|---|")
    for c in cases:
        note = "flaky (passed on retry)" if c.get("flaky") else ""
        o.append(f"| {c['id']} | {cell(c.get('title'), 70)} | {ICON.get(c['verdict'], '')} {c['verdict']} | {note} |")
    bad = [c for c in cases if c["verdict"] in ("FAIL", "BLOCKED")]
    if bad:
        o.append("\n### Failures")
    for c in bad:
        t = c.get("triage") or {}
        o.append(f"\n#### {c['id']} — {c.get('title')}\n- Verdict: **{c['verdict']}** · severity **{t.get('severity', '?')}** · suspected **{t.get('suspect', '?')}** (confidence {t.get('confidence', '?')})")
        if t.get("reasoning"):
            o.append(f"- Reasoning: {t['reasoning']}")
        for s in c.get("steps") or []:
            if s.get("verdict") in ("FAIL", "BLOCKED"):
                o.append(f"- Step {s['id']}: expected `{cell(s.get('expected'), 200)}` — actual `{cell(s.get('actual'), 200)}`")
                if s.get("evidence"):
                    o.append(f"  - evidence: {cell(s['evidence'], 300)}")
        facts = c.get("key_log_facts") or []
        if facts:
            o.append("- Key log facts (extracted; Loki retention is limited):")
            for f in facts[:20]:
                o.append(f"  - {cell(f, 300)}")
    nc = r.get("not_covered") or []
    if nc:
        o.append("\n**Not covered:** " + "; ".join(f"{x.get('what')} ({x.get('why')})" for x in nc))
    fx = r.get("fix") or {}
    o.append(f"\n**Follow-up:** decision `{fx.get('decision', 'none')}`" + (f" · issue {fx['issue_url']}" if fx.get("issue_url") else "") + (f" · MR {fx['mr_url']}" if fx.get("mr_url") else ""))
    if (r.get("notifications") or {}).get("sent"):
        o.append(f"**Notified:** {r['notifications'].get('channel')}")
    sys.stdout.write(redact("\n".join(o)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
