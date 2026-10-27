#!/usr/bin/env python3
"""ensure_weekly_fn.py — self-heal the weekly scheduled fn from inside a netlify build.

Why this exists (S14): production deploys REPLACE the whole function set. The
Oct-18 git build wiped the raw-API-deployed weekly fn (functions_dir was null),
silently killing the Monday 09:00 UTC cron. Two defenses now:
  1. netlify.toml [functions] directory="functions" → every build deploys
     weekly.mjs itself (function set can never be replaced with NOTHING).
  2. this script runs at build END: if the live fn set is missing `weekly` or
     its cron schedule is not registered, re-deploy via the raw API using the
     PREBUILT zip committed at netlify-ops/weekly-fn/weekly.zip (built by
     @netlify/zip-it-and-ship-it — see netlify-ops/README.md; rebuild whenever
     functions/weekly.mjs changes).

Stdlib only (no pip, no npx at build time). Env: NETLIFY_PAT, NETLIFY_SITE_ID
(set account-wide, context=all — present in build runtime).
Exits 0 unless the redeploy outright fails (never blocks the scan report).
"""
import hashlib
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

PAT = os.environ.get("NETLIFY_PAT", "")
SITE = os.environ.get("NETLIFY_SITE_ID", "")
CRON = "0 9 * * 1"
BASE = "https://api.netlify.com/api/v1"
ZIP_PATH = Path(__file__).resolve().parent.parent / "weekly-fn" / "weekly.zip"
REPO = Path(__file__).resolve().parent.parent.parent


def api(path, data=None, method=None, raw=None, timeout=180):
    req = urllib.request.Request(BASE + path,
                                data=(raw if raw is not None else
                                      (json.dumps(data).encode() if data is not None else None)),
                                method=method)
    req.add_header("Authorization", f"Bearer {PAT}")
    if raw is not None:
        req.add_header("Content-Type", "application/octet-stream")
    elif data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, (json.loads(body) if body[:1] in (b"{", b"[") else body)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body[:300]
    except Exception as ex:
        return 0, str(ex)[:200]


def current_state():
    """(fn_names, weekly_has_schedule) of the live site; (None, None) on API failure."""
    s, d = api(f"/sites/{SITE}/functions", timeout=30)
    if s != 200:
        return None, None
    if isinstance(d, dict):
        for k in ("functions", "data"):
            if k in d and isinstance(d[k], list):
                d = d[k]
                break
    if not isinstance(d, list):
        return None, None
    names = [f.get("name") for f in d]
    weekly = next((f for f in d if f.get("name") == "weekly"), None)
    sched = False
    if weekly:
        scheds = weekly.get("schedules") or weekly.get("cron") or []
        sched = bool(scheds)
        if isinstance(scheds, list) and scheds:
            entry = scheds[0]
            sched = (entry.get("cron") if isinstance(entry, dict) else entry) == CRON
    return names, sched


def redeploy():
    fn_zip = ZIP_PATH.read_bytes()
    idx = b"<!doctype html><title>aizg drift monitor</title>\n"
    body = {
        "files": {"index.html": hashlib.sha1(idx).hexdigest()},
        "functions": {"weekly": hashlib.sha256(fn_zip).hexdigest()},
        "function_schedules": [{"name": "weekly", "cron": CRON}],
        "title": "weekly drift monitor fn (ensure)",
    }
    s, d = api(f"/sites/{SITE}/deploys", data=body, method="POST")
    if s not in (200, 201):
        print(f"ensure_weekly_fn: deploy create failed {s}: {str(d)[:200]}", flush=True)
        return 1
    dep = d["id"]
    api(f"/deploys/{dep}/files/index.html", raw=idx, method="PUT")
    s3, r3 = api(f"/deploys/{dep}/functions/weekly?runtime=nodejs18.x&invocation_mode=stream&size={len(fn_zip)}",
                 raw=fn_zip, method="PUT")
    if s3 not in (200, 201):
        print(f"ensure_weekly_fn: fn PUT failed {s3}: {str(r3)[:200]}", flush=True)
        return 1
    for i in range(45):
        time.sleep(4)
        s4, d4 = api(f"/deploys/{dep}", timeout=30)
        state = d4.get("state") if isinstance(d4, dict) else None
        if state in ("ready", "error"):
            print(f"ensure_weekly_fn: redeploy state={state}", flush=True)
            return 0 if state == "ready" else 1
    print("ensure_weekly_fn: deploy never reached ready", flush=True)
    return 1


def main():
    if not PAT or not SITE:
        print("ensure_weekly_fn: NETLIFY_PAT/NETLIFY_SITE_ID not set — skipping (non-fatal)", flush=True)
        return 0
    if not ZIP_PATH.exists():
        print(f"ensure_weekly_fn: {ZIP_PATH} missing — skipping (non-fatal)", flush=True)
        return 0
    names, sched = current_state()
    ok_set = names is not None and "weekly" in names and sched
    if ok_set:
        print(f"ensure_weekly_fn: OK (fns={names}, schedule=registered)", flush=True)
        return 0
    print(f"ensure_weekly_fn: needs heal (fns={names}, schedule={sched}) — redeploying", flush=True)
    rc = redeploy()
    names2, sched2 = current_state()
    print(f"ensure_weekly_fn: post-heal state fns={names2} schedule={sched2}", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
