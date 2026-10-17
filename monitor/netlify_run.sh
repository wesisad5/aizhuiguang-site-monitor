#!/usr/bin/env bash
# netlify_run.sh — weekly monitor build entry (netlify lane).
#
# 1. runs monitor/site_monitor.py (paced fetches; direct egress, proxy fallback)
# 2. drift  -> file/update the GitHub drift issue (emails the repo owner) + exit 1
#    clean  -> green build (heartbeat, no email noise)
# 3. FETCH_LOG env -> per-attempt log (published to pub/ when set for debugging)
set -uo pipefail
export FETCH_LOG="${FETCH_LOG:-/tmp/fetch-log.jsonl}"
mkdir -p pub

python3 monitor/site_monitor.py --report-only > /tmp/scan-report.txt 2>&1
RC=$?
cat /tmp/scan-report.txt
cp "$FETCH_LOG" pub/fetch-log.jsonl 2>/dev/null || echo "[]" > pub/fetch-log.jsonl

STATUS=$(python3 - << 'PY'
import json
try:
    d = json.load(open("monitor/report.json"))
    print(d.get("status", "error"))
except Exception:
    print("error")
PY
)

if [ "$STATUS" = "clean" ] || [ "$STATUS" = "baseline-updated" ]; then
  echo "weekly monitor: $STATUS"
  echo "$STATUS $(date -u +%FT%TZ)" > pub/status.txt
  exit 0
fi

echo "weekly monitor: DRIFT DETECTED ($STATUS) — filing issue"
python3 monitor/file_drift_issue.py || echo "(issue filing failed — see report above)"
echo "drift $(date -u +%FT%TZ)" > pub/status.txt
exit 1
