#!/usr/bin/env bash
# DIAGNOSTIC 4: time sitemap.xml + r.jina.ai + z-ai paths individually.
set -uo pipefail
mkdir -p pub
python3 - << 'PY' > pub/diag4.json 2> pub/diag4-err.txt
import json, ssl, time, urllib.request, urllib.error
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/126"}
CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
out = {}
def timed(name, url, timeout):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            b = r.read()
            out[name] = {"ok": True, "status": r.status, "ms": int((time.time()-t0)*1000), "bytes": len(b), "head": b[:80].decode("utf-8","replace")}
    except urllib.error.HTTPError as e:
        out[name] = {"ok": False, "http": e.code, "ms": int((time.time()-t0)*1000)}
    except Exception as e:
        out[name] = {"ok": False, "err": str(e)[:100], "ms": int((time.time()-t0)*1000)}
timed("sitemap-direct", "https://aizhuiguang.tech/sitemap.xml", 30)
timed("jina-sitemap", "https://r.jina.ai/https://aizhuiguang.tech/sitemap.xml", 30)
timed("sitemap-2nd", "https://aizhuiguang.tech/sitemap.xml", 30)
timed("page-again", "https://aizhuiguang.tech/tools/manju.html", 30)
import shutil
out["z_ai_present"] = bool(shutil.which("z-ai"))
print(json.dumps(out))
PY
cat pub/diag4.json || cat pub/diag4-err.txt
exit 0
