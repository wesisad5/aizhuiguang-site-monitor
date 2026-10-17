#!/usr/bin/env bash
# DIAGNOSTIC build: probe egress + time fetches; ALWAYS exit 0 so pub/ publishes.
set -uo pipefail
mkdir -p pub
python3 - << 'PY' > pub/egress-report.json 2>pub/egress-stderr.txt
import json, ssl, time, urllib.request, urllib.error
SITE = "https://aizhuiguang.tech"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/126"}
CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
out = {}
# 1. direct probe
t0=time.time()
try:
    req = urllib.request.Request(SITE + "/", headers=UA)
    with urllib.request.urlopen(req, timeout=10, context=CTX) as r:
        body = r.read()
        out["direct"] = {"ok": True, "status": r.status, "ms": int((time.time()-t0)*1000), "bytes": len(body)}
except Exception as e:
    out["direct"] = {"ok": False, "err": str(e)[:100], "ms": int((time.time()-t0)*1000)}
# 2. proxy timing (2 pages + 1 data file)
PROXY = "https://aizhuiguang-tech.translate.goog"
for name, path in [("proxy-page","/tools/manju.html"), ("proxy-root","/"), ("proxy-data","/tools/aizg-mini.js")]:
    t0=time.time()
    try:
        req = urllib.request.Request(f"{PROXY}{path}?_x_tr_sl=auto&_x_tr_tl=en", headers=UA)
        with urllib.request.urlopen(req, timeout=45, context=CTX) as r:
            b = r.read()
            out[name] = {"ok": True, "status": r.status, "ms": int((time.time()-t0)*1000), "bytes": len(b)}
    except urllib.error.HTTPError as e:
        out[name] = {"ok": False, "http": e.code, "ms": int((time.time()-t0)*1000)}
    except Exception as e:
        out[name] = {"ok": False, "err": str(e)[:80], "ms": int((time.time()-t0)*1000)}
print(json.dumps(out))
PY
cat pub/egress-report.json || cat pub/egress-stderr.txt
exit 0
