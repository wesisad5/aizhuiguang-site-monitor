#!/usr/bin/env bash
# DIAGNOSTIC 2: run the FULL scan with per-phase timing; always exit 0.
set -uo pipefail
mkdir -p pub
python3 - << 'PY' > pub/scan-timing.json 2> pub/scan-stderr.txt
import json, subprocess, sys, time
t0 = time.time()
r = subprocess.run([sys.executable, "monitor/site_monitor.py", "--report-only"],
                   capture_output=True, text=True, timeout=840)
el = time.time() - t0
out = {
  "elapsed_s": round(el, 1),
  "rc": r.returncode,
  "stdout_tail": r.stdout[-3000:],
  "stderr_tail": r.stderr[-2000:],
}
try:
    out["report_status"] = json.load(open("monitor/report.json"))["status"]
    out["findings_n"] = len(json.load(open("monitor/report.json")).get("findings", []))
except Exception as e:
    out["report_status"] = "unreadable: " + str(e)[:80]
print(json.dumps(out))
PY
cat pub/scan-timing.json
cat pub/scan-stderr.txt || true
exit 0
