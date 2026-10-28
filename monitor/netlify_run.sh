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
# BOTH scan paths are capped and the report-only path gets ONE bounded retry: the
# site burst-rate-limits (~24-50 req -> 429) and netlify KILLS builds at 15 min
# hard ("Command did not finish within the time limit") — so attempt1 (420s) +
# cool-down (45s) + attempt2 (300s) + alert/overhead must fit well inside that.
SCAN_BUDGET_S="${SCAN_BUDGET_S:-420}"
RETRY_BUDGET_S="${RETRY_BUDGET_S:-300}"
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
  echo "baseline refresh $(date -u +%FT%TZ) rc=$RC" > pub/status.txt
  exit 0
fi

# Report-only scan, with ONE retry: the site burst-rate-limits (~24-50 req -> 429)
# and a storm window can blow the 660s budget. A killed scan now dumps a partial
# report (SIGTERM checkpoint in site_monitor.py) — treat that as "retry after a
# cool-down", and after a second failure alert distinctly (scan infra, not drift).
timeout "$SCAN_BUDGET_S" python3 monitor/site_monitor.py --report-only > /tmp/scan-report.txt 2>&1
RC=$?
STATUS=$(python3 - << 'PY'
import json
try:
    d = json.load(open("monitor/report.json"))
    print(d.get("status", "error"))
except Exception:
    print("error")
PY
)
if [ "$STATUS" = "partial" ] || [ ! -f monitor/report.json ]; then
  echo "scan attempt 1 incomplete (status=$STATUS rc=$RC) — cooling down 45s, retrying once"
  cat /tmp/scan-report.txt
  sleep 45
  rm -f monitor/report.json
  timeout "$RETRY_BUDGET_S" python3 monitor/site_monitor.py --report-only > /tmp/scan-report.txt 2>&1
  RC=$?
  STATUS=$(python3 - << 'PY'
import json
try:
    d = json.load(open("monitor/report.json"))
    print(d.get("status", "error"))
except Exception:
    print("error")
PY
)
fi
cat /tmp/scan-report.txt
cp "$FETCH_LOG" pub/fetch-log.jsonl 2>/dev/null || echo "[]" > pub/fetch-log.jsonl
echo "scan rc=$RC budget=${SCAN_BUDGET_S}s status=$STATUS" >> pub/fetch-log.jsonl

if [ "$STATUS" = "partial" ] || [ "$STATUS" = "error" ] || [ ! -f monitor/report.json ]; then
  echo "weekly monitor: SCAN INCOMPLETE after retry (status=$STATUS, rc=$RC) — filing infra issue"
  python3 monitor/file_drift_issue.py || echo "(issue filing failed — fetch-log published below)"
  echo "scan-incomplete $(date -u +%FT%TZ) rc=$RC" > pub/status.txt
  exit 1
fi

if [ "$STATUS" = "clean" ] || [ "$STATUS" = "baseline-updated" ]; then
  echo "weekly monitor: $STATUS"
  echo "$STATUS $(date -u +%FT%TZ) rc=$RC" > pub/status.txt
  exit 0
fi

echo "weekly monitor: DRIFT DETECTED ($STATUS, scan rc=$RC) — filing issue"
python3 monitor/file_drift_issue.py || echo "(issue filing failed — see report above)"
echo "drift $(date -u +%FT%TZ) rc=$RC" > pub/status.txt
exit 1
