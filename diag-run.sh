#!/usr/bin/env bash
# S14 forensic: run the REAL build script with -x tracing, capture everything,
# force exit 0 so the deploy publishes pub/ (errored builds publish nothing).
mkdir -p pub
( set -x; bash -x monitor/netlify_run.sh ) > pub/trace.log 2>&1
RC=$?
echo "trace rc=$RC" >> pub/trace.log
{
  echo "=== ENV ==="; bash --version | head -1; python3 --version
  echo "=== bash opts ==="; set -o | head -20
  echo "=== script file ==="; ls -la monitor/netlify_run.sh; file monitor/netlify_run.sh
  echo "=== git HEAD ==="; git rev-parse HEAD 2>/dev/null || true
} >> pub/trace.log 2>&1
echo "diag-done $(date -u +%FT%TZ)" > pub/status.txt
exit 0
