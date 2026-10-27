#!/usr/bin/env bash
# netlify_run.sh — weekly monitor build entry (netlify lane).
#
# The scan is bounded at SCAN_BUDGET_S (default 660s) so the BUILD itself can
# never hit the 15-min netlify cap — a partial scan still publishes its
# fetch-log + report for diagnosis.
#
# 1. runs monitor/site_monitor.py (adaptive pacing; direct egress, proxy fallback)
# 2. drift  -> file/update the GitHub drift issue (emails the repo owner) + exit 1
#    clean  -> green build (weekly heartbeat, no email noise)
set -uo pipefail
export FETCH_LOG="${FETCH_LOG:-/tmp/fetch-log.jsonl}"
# BOTH scan paths are capped: a hung/backoff-spiraling scan must NEVER exceed the
# netlify build window (a partial scan still publishes its fetch-log + report).
SCAN_BUDGET_S="${SCAN_BUDGET_S:-660}"
mkdir -p pub
rm -f "$FETCH_LOG"

# REFRESH mode (REFRESH_BASELINE=1): re-baseline from a direct netlify scan,
# commit the baseline + captured raw files back to the repos, exit clean.
if [ "${REFRESH_BASELINE:-0}" = "1" ]; then
  echo "refresh mode: re-scanning for a new baseline (direct egress)"
  timeout "${SCAN_BUDGET_S}" python3 monitor/site_monitor.py --update > /tmp/scan-report.txt 2>&1
  RC=$?
  cat /tmp/scan-report.txt
  cp "$FETCH_LOG" pub/fetch-log.jsonl 2>/dev/null || echo "[]" > pub/fetch-log.jsonl
  mkdir -p refresh-capture
  curl -s --max-time 60 -H "User-Agent: Mozilla/5.0"        -o refresh-capture/llms.txt "https://aizhuiguang.tech/llms.txt" || true
  ls -la refresh-capture/ || true
  cp refresh-capture/* pub/ 2>/dev/null || true   # publish raw captures for pickup
  python3 monitor/commit_refresh.py || echo "(refresh commit failed — see above)"
  python3 netlify-ops/ensure_weekly_fn.py || echo "(weekly fn ensure failed — non-fatal)"
  echo "baseline refresh $(date -u +%FT%TZ) rc=$RC" > pub/status.txt
  exit 0
fi

timeout "$SCAN_BUDGET_S" python3 monitor/site_monitor.py --report-only > /tmp/scan-report.txt 2>&1
RC=$?
cat /tmp/scan-report.txt
cp "$FETCH_LOG" pub/fetch-log.jsonl 2>/dev/null || echo "[]" > pub/fetch-log.jsonl
echo "scan rc=$RC budget=${SCAN_BUDGET_S}s" >> pub/fetch-log.jsonl

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
  python3 netlify-ops/ensure_weekly_fn.py || echo "(weekly fn ensure failed — non-fatal)"
  echo "$STATUS $(date -u +%FT%TZ) rc=$RC" > pub/status.txt
  exit 0
fi

echo "weekly monitor: DRIFT DETECTED ($STATUS, scan rc=$RC) — filing issue"
python3 monitor/file_drift_issue.py || echo "(issue filing failed — see report above)"
python3 netlify-ops/ensure_weekly_fn.py || echo "(weekly fn ensure failed — non-fatal)"
echo "drift $(date -u +%FT%TZ) rc=$RC" > pub/status.txt
exit 1
