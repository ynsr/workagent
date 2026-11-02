#!/usr/bin/env python3
"""Poll a version URL until the released build is live.

Usage: wait_for_version.py --url URL --expect VALUE [--match contains|exact|regex]
                           [--timeout 900] [--interval 15] [--consecutive 3]
Prints one JSON line: {status: matched|timeout|error, observed, waited_s, attempts}
Exit: 0 matched, 1 timeout, 2 usage/error.
"""
import argparse, json, re, sys, time, urllib.request, urllib.error


def ok(observed: str, expect: str, mode: str) -> bool:
    if mode == "exact":
        return observed.strip() == expect
    if mode == "regex":
        return re.search(expect, observed) is not None
    return expect in observed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--expect", required=True)
    ap.add_argument("--match", default="contains", choices=["contains", "exact", "regex"])
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--interval", type=int, default=15)
    ap.add_argument("--consecutive", type=int, default=3)
    a = ap.parse_args()
    if not a.url.startswith("https://"):
        print(json.dumps({"status": "error", "error": "url must be https"}))
        return 2
    start, streak, attempts, observed = time.time(), 0, 0, ""
    while True:
        attempts += 1
        try:
            req = urllib.request.Request(a.url, headers={"Cache-Control": "no-cache", "User-Agent": "workagent-prodtest"})
            with urllib.request.urlopen(req, timeout=10) as r:
                observed = r.read(4096).decode("utf-8", "replace").strip()
            streak = streak + 1 if ok(observed, a.expect, a.match) else 0
        except (urllib.error.URLError, OSError) as e:
            observed, streak = f"<error: {e}>", 0
        waited = int(time.time() - start)
        if streak >= a.consecutive:
            print(json.dumps({"status": "matched", "observed": observed[:200], "waited_s": waited, "attempts": attempts}))
            return 0
        if waited + a.interval > a.timeout:
            print(json.dumps({"status": "timeout", "observed": observed[:200], "waited_s": waited, "attempts": attempts}))
            return 1
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
