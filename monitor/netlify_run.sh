#!/usr/bin/env bash
# DIAGNOSTIC 5: full attempt log (success+failure) with elapsed timestamps, 150s window.
set -uo pipefail
mkdir -p pub
rm -f /tmp/fetch-log.jsonl
timeout 150 python3 monitor/site_monitor.py --report-only > /tmp/scan-out.txt 2>&1
RC=$?
cp /tmp/fetch-log.jsonl pub/fetch-log.jsonl 2>/dev/null || echo "[]" > pub/fetch-log.jsonl
head -c 800 /tmp/scan-out.txt > pub/scan-out-head.txt 2>/dev/null || true
python3 - << 'PY' > pub/diag5.json
import json
log = [json.loads(l) for l in open("pub/fetch-log.jsonl") if l.strip()]
gaps = []
prev = 0.0
for f in log:
    gaps.append(round(f["el_s"] - prev, 1))
    prev = f["el_s"]
out = {
  "n_attempts": len(log),
  "last_el_s": log[-1]["el_s"] if log else 0,
  "gaps_s": gaps,
  "failures": [f for f in log if f["status"] != 200],
  "log": log,
}
print(json.dumps(out))
PY
cat pub/diag5.json | head -c 300
exit 0
