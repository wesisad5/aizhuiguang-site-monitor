#!/usr/bin/env python3
"""ensure_weekly_fn.py — MANUAL repair tool for the cron-hub weekly fn (S15).

History: written in S14 as an in-build self-heal for the lane sites — DISPROVEN
(the build's own deploy finalizes AFTER the script and re-wipes the set; see
netlify-ops/README.md "Cron hub"). The lane sites now carry NO functions at
all; the scheduled fn lives on aizg-vault (no git, no builds — nothing can
replace its set).

Use TODAY: run manually (sandbox) if the hub's fn or schedule ever looks wrong:
  NETLIFY_PAT=<wesisadora005 PAT> NETLIFY_SITE_ID=84a4ed19-7df7-4c44-bb3c-137a0d5f3d6a \
      python3 netlify-ops/ensure_weekly_fn.py
It re-deploys the PREBUILT zip committed at netlify-ops/weekly-fn/weekly.zip
(rebuild it with @netlify/zip-it-and-ship-it whenever functions/weekly.mjs
changes — or just use the scrape repo's scripts/s15-deploy_cron_hub.py which
rebuilds + redeploys + sets env in one go).

Stdlib only. Exits 0 unless the redeploy outright fails.
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
CRON = "7 17 * * 1"  # S15: Mon 17:07 UTC (see netlify-ops/README.md postmortem S15)
BASE = "https://api.netlify.com/api/v1"
ZIP_PATH = Path(__file__).resolve().parent / "weekly-fn" / "weekly.zip"


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
    """(fn_names, weekly_has_schedule) of the live site; (None, None) on API failure.
    NOTE the endpoint's abbreviated keys: name='n', schedule='schedule' (plain string)."""
    s, d = api(f"/sites/{SITE}/functions", timeout=30)
    if s != 200 or not isinstance(d, dict):
        return None, None
    fns = d.get("functions")
    if not isinstance(fns, list):
        return None, None
    names = [f.get("n") or f.get("name") for f in fns]
    weekly = next((f for f in fns if (f.get("n") or f.get("name")) == "weekly"), None)
    sched = False
    if weekly:
        raw = weekly.get("schedule") or weekly.get("schedules") or weekly.get("cron")
        if isinstance(raw, str):
            sched = raw == CRON
        elif isinstance(raw, list):
            entry = raw[0] if raw else {}
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
