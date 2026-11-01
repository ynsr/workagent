#!/usr/bin/env python3
"""Validate a prod-test plan (format + safety policy against the repo manifest).

Usage: validate_plan.py PLAN.yaml [--manifest .workagent/prodtest.yaml] [--json]
Exit codes: 0 valid (warnings allowed), 1 invalid, 2 usage / IO error.
Requires: PyYAML.
"""
import argparse, json, re, sys

try:
    import yaml
except ImportError:
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

RISK = {"low", "medium", "high", "critical"}
KINDS = {"smoke", "acceptance", "regression", "negative"}
SAFETY = {"read-only", "mutating-test-data"}
PRIORITY = {"must", "should", "nice"}
SEVERITY = {"low", "medium", "high", "critical"}
SUSPECT = {"code", "data", "env", "test"}
TOOLS = {"admin_api", "http", "sql", "lokiq"}
EXPECT = {"status", "jsonpath", "sql_rows", "sql_value", "log_contains",
          "log_absent", "duration_ms_lt", "header", "body_contains"}
HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}
SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|merge|call|copy|vacuum|upsert)\b", re.I)
SECRET_PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}"), "bearer token"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "JWT"),
    (re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*(?!\$\{)[^\s,;}]{6,}"), "inline credential"),
]
CARD_RE = re.compile(r"(?<!\d)\d{13,19}(?!\d)")


def luhn(num: str) -> bool:
    total, alt = 0, False
    for ch in reversed(num):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--manifest")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    errors, warns = [], []
    err, warn = errors.append, warns.append

    try:
        raw = open(a.plan, encoding="utf-8").read()
        plan = yaml.safe_load(raw)
        manifest = yaml.safe_load(open(a.manifest, encoding="utf-8")) if a.manifest else None
    except (OSError, yaml.YAMLError) as e:
        print(f"cannot read input: {e}", file=sys.stderr)
        return 2
    if not isinstance(plan, dict):
        print("plan must be a YAML mapping", file=sys.stderr)
        return 1

    if plan.get("plan_version") != 1:
        err("plan_version must be 1")
    for k in ("issue", "summary"):
        if not plan.get(k):
            err(f"missing '{k}'")
    if (plan.get("risk") or {}).get("level") not in RISK:
        err(f"risk.level must be one of {sorted(RISK)}")
    if not isinstance(plan.get("change_kinds"), list) or not plan["change_kinds"]:
        err("change_kinds must be a non-empty list")
    b = plan.get("budget") or {}
    if not (isinstance(b.get("max_cases"), int) and isinstance(b.get("max_minutes"), int)):
        err("budget.max_cases and budget.max_minutes must be integers")
    base = plan.get("base") or {}
    if not base.get("head_sha") or not base.get("diff_hash"):
        warn("base.head_sha / base.diff_hash missing: staleness check impossible")
    if "not_covered" not in plan:
        err("'not_covered' must be present (use [] if everything is covered)")

    cases = plan.get("cases") or []
    smoke_refs = plan.get("smoke") or []
    if not cases and not smoke_refs:
        err("plan has neither cases nor smoke references")
    if isinstance(b.get("max_cases"), int) and len(cases) + len(smoke_refs) > b["max_cases"]:
        err(f"{len(cases)} cases + {len(smoke_refs)} smoke exceed budget.max_cases={b['max_cases']}")

    m_subjects = (manifest or {}).get("subjects") or {}
    m_tools = (manifest or {}).get("tools") or {}
    m_smoke = {c.get("id") for c in (manifest or {}).get("smoke_cases") or [] if isinstance(c, dict)}
    proposed = {p.get("id") for p in plan.get("proposed_subjects") or [] if isinstance(p, dict)}

    if manifest is not None:
        for s in plan.get("requires_subjects") or []:
            if s not in m_subjects:
                (warn if s in proposed else err)(
                    f"requires_subjects '{s}' not in manifest.subjects" + (" (proposed: admin must create it)" if s in proposed else ""))
        for r in smoke_refs:
            if r not in m_smoke:
                err(f"smoke ref '{r}' not defined in manifest.smoke_cases")

    seen = set()
    for i, c in enumerate(cases):
        cid = c.get("id", f"#{i}")
        p = f"case {cid}"
        if not re.fullmatch(r"TC\d+", str(c.get("id", ""))):
            err(f"{p}: id must match TC<number>")
        if cid in seen:
            err(f"{p}: duplicate id")
        seen.add(cid)
        if not c.get("title"):
            err(f"{p}: missing title")
        if c.get("kind") not in KINDS:
            err(f"{p}: kind must be one of {sorted(KINDS)}")
        if c.get("safety") not in SAFETY:
            err(f"{p}: safety must be one of {sorted(SAFETY)}")
        if c.get("priority") not in PRIORITY:
            err(f"{p}: priority must be one of {sorted(PRIORITY)}")
        covers = c.get("covers") or {}
        if not (covers.get("acceptance") or covers.get("files")):
            err(f"{p}: 'covers' needs acceptance and/or files (traceability)")
        subj = c.get("subject")
        if not subj:
            err(f"{p}: missing subject")
        elif manifest is not None:
            if subj not in m_subjects:
                (warn if subj in proposed else err)(f"{p}: subject '{subj}' not in manifest.subjects")
            elif c.get("safety") == "mutating-test-data":
                if (m_subjects[subj] or {}).get("mutating_ok") is not True:
                    err(f"{p}: mutating case on subject '{subj}' which has mutating_ok != true")
        fails = c.get("if_fails") or {}
        if fails.get("severity") not in SEVERITY:
            err(f"{p}: if_fails.severity must be one of {sorted(SEVERITY)}")
        if fails.get("suspect_hint") not in SUSPECT:
            err(f"{p}: if_fails.suspect_hint must be one of {sorted(SUSPECT)}")
        steps = c.get("steps") or []
        if not steps:
            err(f"{p}: no steps")
        sids = set()
        for j, s in enumerate(steps):
            sp = f"{p} step {s.get('id', j)}"
            if not s.get("id") or s["id"] in sids:
                err(f"{sp}: missing or duplicate step id")
            sids.add(s.get("id"))
            tool = s.get("tool")
            if tool not in TOOLS:
                err(f"{sp}: tool must be one of {sorted(TOOLS)}")
            elif manifest is not None and tool not in m_tools:
                err(f"{sp}: tool '{tool}' not configured in manifest.tools")
            req = s.get("request") or {}
            exp = s.get("expect") or []
            if not exp:
                err(f"{sp}: needs at least one machine-checkable 'expect'")
            for e in exp:
                if e.get("type") not in EXPECT:
                    err(f"{sp}: expect.type must be one of {sorted(EXPECT)}")
            if tool in ("admin_api", "http"):
                method = str(req.get("method", "")).upper()
                if method not in HTTP_METHODS:
                    err(f"{sp}: request.method required ({sorted(HTTP_METHODS)})")
                elif c.get("safety") == "read-only" and method not in ("GET", "HEAD") and not s.get("reads_only_justification"):
                    err(f"{sp}: non-GET in a read-only case needs 'reads_only_justification'")
                amt = req.get("amount")
                if amt is not None and manifest is not None and subj in m_subjects:
                    cap = (m_subjects[subj] or {}).get("max_amount")
                    if cap is None:
                        err(f"{sp}: amount used but subject '{subj}' has no max_amount in manifest")
                    elif amt > cap:
                        err(f"{sp}: amount {amt} exceeds subject max_amount {cap}")
            if tool == "sql":
                body = str(req.get("query", "")).strip().rstrip(";")
                if not re.match(r"(?is)^\s*(select|with)\b", body):
                    err(f"{sp}: SQL must start with SELECT/WITH")
                if ";" in body:
                    err(f"{sp}: multiple SQL statements are not allowed")
                if SQL_FORBIDDEN.search(body):
                    err(f"{sp}: SQL contains a write/DDL keyword")

    for rx, label in SECRET_PATTERNS:
        if rx.search(raw):
            err(f"possible secret in plan ({label}); use ${{ENV_NAME}} references")
    for m in CARD_RE.finditer(raw):
        if luhn(m.group()):
            err("possible card number (Luhn-valid 13-19 digit value) in plan")
            break

    result = {"valid": not errors, "errors": errors, "warnings": warns,
              "cases": len(cases), "smoke": len(smoke_refs)}
    if a.json:
        print(json.dumps(result))
    else:
        for e in errors:
            print(f"ERROR: {e}")
        for w in warns:
            print(f"WARN:  {w}")
        print("VALID" if not errors else f"INVALID ({len(errors)} errors)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
