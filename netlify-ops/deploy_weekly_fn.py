#!/usr/bin/env python3
"""Raw-API deploy of the weekly.mjs scheduled function (no CLI — its hasha
worker threads EAGAIN in this sandbox).

Netlify contract (from @netlify/open-api swagger):
  POST /sites/{id}/deploys {files: {path: sha1}, functions: {name: sha1},
                            function_schedules: [{name, cron}]}
  PUT  /deploys/{id}/files/{path}          raw body
  PUT  /deploys/{id}/functions/{name}      raw body = ZIP of the function
"""
import hashlib
import io
import json
import sys
import time
import urllib.request
import urllib.error
import zipfile

import os
PAT = os.environ.get('NETLIFY_PAT', '')
SITE = os.environ.get('NETLIFY_SITE_ID', '')
BASE = 'https://api.netlify.com/api/v1'
CRON = '0 9 * * 1'   # Monday 09:00 UTC


def api(path, data=None, method=None, raw=None):
    req = urllib.request.Request(BASE + path,
                                 data=(raw if raw is not None else
                                       (json.dumps(data).encode() if data is not None else None)),
                                 method=method)
    req.add_header('Authorization', f'Bearer {PAT}')
    if raw is not None:
        req.add_header('Content-Type', 'application/octet-stream')
    elif data is not None:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            body = r.read()
            return r.status, (json.loads(body) if body[:1] in (b'{', b'[') else body)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body[:300]


def main():
    # 1. build function zip (single-file v2 function: weekly.mjs at zip root)
    src = open('/home/z/work/monitor-repo/functions/weekly.mjs', 'rb').read()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('weekly.mjs', src)
    fn_zip = buf.getvalue()
    idx = b'<!doctype html><title>aizg drift monitor</title>\n'
    print(f'function zip: {len(fn_zip)}B, index: {len(idx)}B')

    body = {
        'files': {'index.html': hashlib.sha1(idx).hexdigest()},
        'functions': {'weekly': hashlib.sha1(fn_zip).hexdigest()},
        'function_schedules': [{'name': 'weekly', 'cron': CRON}],
        'title': 'weekly drift monitor fn',
    }
    s, d = api(f'/sites/{SITE}/deploys', data=body, method='POST')
    print('deploy created:', s, d.get('id') if isinstance(d, dict) else d)
    if s not in (200, 201):
        print(json.dumps(d)[:400] if isinstance(d, dict) else d)
        return 1
    dep = d['id']

    s2, r2 = api(f'/deploys/{dep}/files/index.html', raw=idx, method='PUT')
    print('file PUT:', s2)
    if s2 not in (200, 201):
        print(r2 if isinstance(r2, dict) else r2)
        return 1
    s3, r3 = api(f'/deploys/{dep}/functions/weekly', raw=fn_zip, method='PUT')
    print('function PUT:', s3)
    if s3 not in (200, 201):
        print(r3 if isinstance(r3, dict) else r3)
        return 1

    for i in range(60):
        time.sleep(4)
        s4, d4 = api(f'/deploys/{dep}')
        state = d4.get('state') if isinstance(d4, dict) else None
        if state in ('ready', 'error'):
            print('deploy state:', state)
            if state == 'error':
                print(json.dumps(d4)[:600] if isinstance(d4, dict) else d4)
                return 1
            print('LIVE (prod): https://aizg-drift-monitor.netlify.app/api/weekly')
            return 0
        if i % 5 == 0:
            print('  ...', state, flush=True)
    print('never ready')
    return 1


if __name__ == '__main__':
    sys.exit(main())
