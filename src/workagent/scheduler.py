"""Built-in recurring Cron/RRULE scheduler & lease-safe due-run dispatcher (T13).

Supports:
- Standard 5-field cron expressions (`minute hour dom month dow`) with
  `*`, `*/n`, `a-b`, `a-b/n`, `a,b`, named months (`JAN`..`DEC`), named
  weekdays (`SUN`..`SAT`, 0 or 7 = Sunday), and `@hourly`/`@daily`/`@weekly`/`@monthly`.
- Basic `RRULE:` expressions (`FREQ=HOURLY|DAILY|WEEKLY|MONTHLY`, `INTERVAL`,
  `BYHOUR`, `BYMINUTE`, `BYDAY=MO,TU,...`).
- IANA timezones via stdlib `zoneinfo.ZoneInfo`.
- Atomic SQLite lease claiming so concurrent dispatchers never double-fire.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import store_sqlite as _sq

logger = logging.getLogger(__name__)

_MACROS: dict[str, str] = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
}

_MONTH_NAMES: dict[str, int] = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Cron dow: 0=Sun, 1=Mon, ..., 6=Sat (7 also accepted as Sun)
_DOW_NAMES: dict[str, int] = {
    "SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6,
}

_RRULE_DAY_TO_CRON: dict[str, int] = {
    "SU": 0, "MO": 1, "TU": 2, "WE": 3, "TH": 4, "FR": 5, "SA": 6,
}

STARTER_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "daily-unreviewed-pr-review",
        "name": "Daily unreviewed PR review",
        "description": "Review all open unreviewed PRs/MRs every weekday morning.",
        "cron_expr": "0 9 * * 1-5",
        "timezone": "UTC",
        "target_kind": "command",
        "target_label": "review --all --yes",
        "args_json": "{}",
    },
    {
        "id": "daily-merged-worktree-cleanup",
        "name": "Daily merged worktree cleanup",
        "description": "Clean up merged worktrees every evening.",
        "cron_expr": "0 20 * * *",
        "timezone": "UTC",
        "target_kind": "command",
        "target_label": "cleanup --merged --yes",
        "args_json": "{}",
    },
    {
        "id": "post-release-prodtest-sweep",
        "name": "Post-release prodtest sweep",
        "description": "Run pending production test plans every 6 hours.",
        "cron_expr": "0 */6 * * *",
        "timezone": "UTC",
        "target_kind": "command",
        "target_label": "prodtest run --all --yes",
        "args_json": "{}",
    },
]


def validate_timezone(tz_name: str) -> ZoneInfo:
    """Validate an IANA timezone string and return its ZoneInfo."""
    name = (tz_name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as e:
        raise ValueError(f"invalid timezone {tz_name!r}") from e


def _parse_cron_field(
    raw: str,
    min_val: int,
    max_val: int,
    names: dict[str, int] | None = None,
    *,
    is_dow: bool = False,
) -> set[int]:
    token = raw.strip().upper()
    if not token:
        raise ValueError("empty cron field")
    result: set[int] = set()
    for part in token.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"invalid cron segment in {raw!r}")
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) <= 0:
                raise ValueError(f"invalid cron step {step_s!r} in {raw!r}")
            step = int(step_s)
        else:
            base = part

        if base == "*":
            lo, hi = min_val, max_val
        elif "-" in base:
            lo_s, hi_s = base.split("-", 1)
            lo = _parse_atom(lo_s, min_val, max_val, names, is_dow=is_dow)
            hi = _parse_atom(hi_s, min_val, max_val, names, is_dow=is_dow)
            if lo > hi:
                raise ValueError(f"cron range start > end in {raw!r}")
        else:
            val = _parse_atom(base, min_val, max_val, names, is_dow=is_dow)
            lo, hi = val, val

        for v in range(lo, hi + 1, step):
            result.add(0 if (is_dow and v == 7) else v)

    if not result:
        raise ValueError(f"cron field {raw!r} matched no values")
    return result


def _parse_atom(
    s: str,
    min_val: int,
    max_val: int,
    names: dict[str, int] | None,
    *,
    is_dow: bool = False,
) -> int:
    s = s.strip().upper()
    if names and s in names:
        return names[s]
    if not s.isdigit():
        raise ValueError(f"invalid cron value {s!r}")
    v = int(s)
    hi = 7 if is_dow else max_val
    if v < min_val or v > hi:
        raise ValueError(f"cron value {v} out of range [{min_val}, {hi}]")
    return v


def _rrule_to_cron(expr: str) -> str:
    """Convert basic RRULE:FREQ=... into an equivalent 5-field cron expression.

    Supported subset: FREQ (HOURLY/DAILY/WEEKLY/MONTHLY), INTERVAL (=1 for
    non-HOURLY), BYMINUTE, BYHOUR, BYDAY (WEEKLY), BYMONTHDAY (MONTHLY).
    Unsupported keys (COUNT/UNTIL/DTSTART and anything else) raise ValueError:
    bounded/recurrence-anchored rules cannot be expressed as cron and must
    not silently run forever. INTERVAL>1 for DAILY/WEEKLY/MONTHLY is rejected
    (cron */N on dom/month is not every-N-days; true interval iteration is
    not implemented yet).
    """
    body = expr.strip()
    if body.upper().startswith("RRULE:"):
        body = body[6:]
    parts: dict[str, str] = {}
    for item in body.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"invalid RRULE token {item!r}")
        k, v = item.split("=", 1)
        parts[k.strip().upper()] = v.strip().upper()

    allowed = {"FREQ", "INTERVAL", "BYMINUTE", "BYHOUR", "BYDAY", "BYMONTHDAY"}
    unknown = sorted(k for k in parts if k not in allowed)
    if unknown:
        raise ValueError(
            f"unsupported RRULE key(s) {', '.join(unknown)} in {expr!r} "
            "(supported: FREQ, INTERVAL, BYMINUTE, BYHOUR, BYDAY, BYMONTHDAY)")

    freq = parts.get("FREQ", "")
    interval = int(parts.get("INTERVAL", "1")) if parts.get("INTERVAL", "1").isdigit() else 0
    if interval <= 0:
        raise ValueError(f"invalid RRULE INTERVAL in {expr!r}")
    if interval > 1 and freq in ("DAILY", "WEEKLY", "MONTHLY"):
        raise ValueError(
            f"RRULE INTERVAL={interval} not supported for FREQ={freq} "
            "(cron */N narrows the schedule instead of spacing occurrences)")

    byminute = parts.get("BYMINUTE", "0")
    byhour = parts.get("BYHOUR", "0")
    byday = parts.get("BYDAY", "")
    bymonthday = parts.get("BYMONTHDAY", "1")

    if freq == "HOURLY":
        hr = "*" if interval == 1 else f"*/{interval}"
        return f"{byminute} {hr} * * *"
    if freq == "DAILY":
        return f"{byminute} {byhour} * * *"
    if freq == "WEEKLY":
        if byday:
            days = []
            for d in byday.split(","):
                d = d.strip()
                if d not in _RRULE_DAY_TO_CRON:
                    raise ValueError(f"invalid RRULE BYDAY {d!r}")
                days.append(str(_RRULE_DAY_TO_CRON[d]))
            dow = ",".join(days)
        else:
            dow = "1"
        return f"{byminute} {byhour} * * {dow}"
    if freq == "MONTHLY":
        return f"{byminute} {byhour} {bymonthday} * *"
    raise ValueError(f"unsupported RRULE FREQ {freq!r} (supported: HOURLY, DAILY, WEEKLY, MONTHLY)")


def parse_schedule_expr(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int], bool, bool]:
    """Parse a 5-field cron or RRULE expression.

    Returns (minutes, hours, doms, months, dows, dom_star, dow_star).
    """
    raw = (expr or "").strip()
    if not raw:
        raise ValueError("cron_expr is required")
    lower = raw.lower()
    if lower in _MACROS:
        raw = _MACROS[lower]
    elif raw.upper().startswith("RRULE:") or raw.upper().startswith("FREQ="):
        raw = _rrule_to_cron(raw)

    fields = raw.split()
    if len(fields) != 5:
        raise ValueError(f"cron_expr must have 5 fields (got {len(fields)}: {expr!r})")
    f_min, f_hour, f_dom, f_mon, f_dow = fields
    minutes = _parse_cron_field(f_min, 0, 59)
    hours = _parse_cron_field(f_hour, 0, 23)
    doms = _parse_cron_field(f_dom, 1, 31)
    months = _parse_cron_field(f_mon, 1, 12, _MONTH_NAMES)
    dows = _parse_cron_field(f_dow, 0, 6, _DOW_NAMES, is_dow=True)
    return minutes, hours, doms, months, dows, f_dom.strip() == "*", f_dow.strip() == "*"


def _cron_dow(dt: datetime) -> int:
    # Python weekday(): Mon=0..Sun=6 -> Cron: Sun=0, Mon=1..Sat=6
    return (dt.weekday() + 1) % 7


def next_run_after(
    expr: str,
    tz_name: str = "UTC",
    after: datetime | None = None,
) -> datetime:
    """Compute the next UTC occurrence strictly after *after*."""
    tz = validate_timezone(tz_name)
    minutes, hours, doms, months, dows, dom_star, dow_star = parse_schedule_expr(expr)

    base_utc = (after or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # Start searching from the next whole minute in target timezone
    cur = (base_utc.astimezone(tz).replace(second=0, microsecond=0) + timedelta(minutes=1))
    limit_year = cur.year + 5

    while cur.year <= limit_year:
        if cur.month not in months:
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                cur = cur.replace(month=cur.month + 1, day=1, hour=0, minute=0)
            continue

        dom_ok = cur.day in doms
        dow_ok = _cron_dow(cur) in dows
        if dom_star and dow_star:
            day_ok = True
        elif dom_star:
            day_ok = dow_ok
        elif dow_star:
            day_ok = dom_ok
        else:
            # Standard Vixie cron: if both dom and dow are restricted, either matches
            day_ok = dom_ok or dow_ok

        if not day_ok:
            cur = (cur + timedelta(days=1)).replace(hour=0, minute=0)
            continue

        if cur.hour not in hours:
            cur = (cur + timedelta(hours=1)).replace(minute=0)
            continue

        if cur.minute not in minutes:
            cur = cur + timedelta(minutes=1)
            continue

        return cur.astimezone(timezone.utc)

    raise ValueError(f"no matching occurrence within 5 years for {expr!r}")


def preview_next_runs(
    expr: str,
    tz_name: str = "UTC",
    count: int = 5,
    after: datetime | None = None,
) -> list[str]:
    """Return the next *count* ISO-8601 UTC timestamps for *expr* in *tz_name*."""
    n = max(1, min(int(count), 20))
    results: list[str] = []
    cursor = after or datetime.now(timezone.utc)
    for _ in range(n):
        nxt = next_run_after(expr, tz_name, after=cursor)
        results.append(nxt.isoformat())
        cursor = nxt
    return results


def build_schedule_argv(schedule: dict) -> list[str]:
    """Build the `workagent` CLI argv list for a schedule row."""
    kind = (schedule.get("target_kind") or "").strip()
    label = (schedule.get("target_label") or "").strip()
    args = schedule.get("args")
    if args is None:
        args = {}

    if kind == "flow":
        argv = ["flow", "run", label]
    elif kind == "action":
        argv = ["action", "run", label]
    elif kind == "command":
        argv = shlex.split(label)
    else:
        raise ValueError(f"unsupported target_kind {kind!r}")

    if isinstance(args, list):
        argv.extend(str(a) for a in args)
    elif isinstance(args, dict):
        # Optional positional ref first (for flow/action targets)
        ref = args.get("ref")
        if ref:
            argv.append(str(ref))
        extra_list = args.get("argv")
        if isinstance(extra_list, list):
            argv.extend(str(a) for a in extra_list)
        for k, v in args.items():
            if k in ("ref", "argv") or v is None or v is False:
                continue
            flag = k if k.startswith("-") else f"--{k.replace('_', '-')}"
            if v is True:
                argv.append(flag)
            else:
                argv.extend([flag, str(v)])

    return argv


def _default_dispatcher(schedule: dict, argv: list[str]) -> int | None:
    """Spawn `sys.executable -m workagent <argv>` detached/non-blocking."""
    subprocess.Popen(
        [sys.executable, "-m", "workagent", *argv],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return None


def run_due_schedules(
    db_path: Path | None = None,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    lease_seconds: int = 60,
    dispatcher: Callable[[dict, list[str]], int | None] | None = None,
) -> list[dict]:
    """Find and dispatch all due enabled schedules (lease-safe).

    Returns a list of result dicts: {id, name, argv, status, run_id, next_run_at, error}.
    """
    path = db_path or _sq.db_path()
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_iso = now_dt.isoformat()
    lease_until_iso = (now_dt + timedelta(seconds=max(5, int(lease_seconds)))).isoformat()

    if dry_run:
        due = [
            s for s in _sq.list_schedules(path)
            if s.get("enabled")
            and s.get("next_run_at")
            and s["next_run_at"] <= now_iso
            and (not s.get("lease_until") or s["lease_until"] <= now_iso)
        ]
        out: list[dict] = []
        for s in due:
            argv = build_schedule_argv(s)
            nxt = next_run_after(s["cron_expr"], s["timezone"], after=now_dt).isoformat()
            out.append({
                "id": s["id"],
                "name": s["name"],
                "argv": argv,
                "status": "dry_run",
                "run_id": None,
                "next_run_at": nxt,
                "error": None,
            })
        return out

    claimed = _sq.claim_due_schedules(path, now_iso=now_iso, lease_until_iso=lease_until_iso)
    dispatch = dispatcher or _default_dispatcher
    results: list[dict] = []
    for s in claimed:
        argv: list[str] = []
        run_id: int | None = None
        err: str | None = None
        status = "dispatched"
        try:
            argv = build_schedule_argv(s)
            run_id = dispatch(s, argv)
        except Exception as e:
            status = "error"
            err = str(e)
            logger.warning("schedule %s (%s) dispatch failed: %s", s["id"], s["name"], e)
        try:
            nxt = next_run_after(s["cron_expr"], s["timezone"], after=now_dt).isoformat()
        except Exception as e:
            # Keep the old next_run_at so the row stays due and visible (no
            # silent wedge); surfacing the cron error beats persisting "".
            nxt = s.get("next_run_at") or ""
            if status == "dispatched":
                status = "error"
            err = "; ".join(x for x in (err, f"next-run recompute failed: {e}") if x)
            logger.warning("schedule %s (%s) next-run failed: %s", s["id"], s["name"], e)
        _sq.record_schedule_run(
            path,
            s["id"],
            last_run_at=now_iso,
            next_run_at=nxt,
            last_run_id=run_id,
        )
        results.append({
            "id": s["id"],
            "name": s["name"],
            "argv": argv,
            "status": status,
            "run_id": run_id,
            "next_run_at": nxt,
            "error": err,
        })
    return results


DEFAULT_SCHEDULER_POLL_INTERVAL = 15.0


class SchedulerLoop:
    """Background daemon thread that dispatches due schedules inside `workagent serve`."""

    def __init__(
        self,
        db_path_fn: Callable[[], Path] | None = None,
        *,
        poll_interval: float = DEFAULT_SCHEDULER_POLL_INTERVAL,
        dispatcher: Callable[[dict, list[str]], int | None] | None = None,
    ) -> None:
        self._db_path_fn = db_path_fn or _sq.db_path
        self.poll_interval = max(1.0, float(poll_interval))
        self._dispatcher = dispatcher
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="workagent-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                db = self._db_path_fn()
                if db.exists():
                    run_due_schedules(db, dispatcher=self._dispatcher)
            except Exception as e:
                logger.debug("scheduler tick failed: %s", e)
            self._stop.wait(self.poll_interval)
