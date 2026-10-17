#!/usr/bin/env bash
# netlify_run.sh — weekly monitor build entry (netlify lane).
#
# 1. runs monitor/site_monitor.py (stdlib; direct egress, proxy fallback)
# 2. on drift: opens/updates the "site drift detected" issue in the public
#    monitor repo via the GitHub API (GH_PAT env) — that emails the repo
#    owner — then exits 1 so the build FAILS (netlify deploy notification).
# 3. on clean scan: exit 0 (green build = heartbeat, visible in the deploys
#    list; no email noise).
set -uo pipefail

MON="monitor/site_monitor.py"
python3 "$MON" --report-only > /tmp/scan-report.txt 2>&1
RC=$?
cat /tmp/scan-report.txt

STATUS=$(python3 - << 'PY'
import json
try:
    d = json.load(open("monitor/report.json"))
    print(d.get("status", "error"))
except Exception as e:
    print("error")
PY
)

if [ "$STATUS" = "ok" ]; then
  echo "weekly monitor: site unchanged (clean heartbeat)"
  mkdir -p pub && echo "ok $(date -u +%FT%TZ)" > pub/status.txt
  exit 0
fi

echo "weekly monitor: DRIFT DETECTED — filing issue"
python3 monitor/file_drift_issue.py || echo "(issue filing failed — see report above)"

mkdir -p pub && echo "drift $(date -u +%FT%TZ)" > pub/status.txt
exit 1
