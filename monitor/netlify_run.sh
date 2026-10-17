#!/usr/bin/env bash
# DIAGNOSTIC 3: scan with per-fetch log, killed at 150s; publish partial log.
set -uo pipefail
mkdir -p pub
rm -f /tmp/fetch-log.jsonl
timeout 150 python3 monitor/site_monitor.py --report-only > /tmp/scan-out.txt 2>&1
RC=$?
cp /tmp/fetch-log.jsonl pub/fetch-log.jsonl 2>/dev/null || echo "[]" > pub/fetch-log.jsonl
python3 - << 'PY' > pub/diag3.json
import json
log = [json.loads(l) for l in open("pub/fetch-log.jsonl") if l.strip()]
out = {
  "n_fetches": len(log),
  "total_ms": sum(f["ms"] for f in log),
  "slowest": sorted(log, key=lambda f: -f["ms"])[:15],
  "tail": log[-5:],
}
try:
    out["report_status"] = json.load(open("monitor/report.json"))["status"]
except Exception as e:
    out["report_status"] = "unfinished: " + str(e)[:60]
print(json.dumps(out))
PY
cat pub/diag3.json
exit 0
