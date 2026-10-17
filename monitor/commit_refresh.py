#!/usr/bin/env python3
"""commit_refresh.py — commit the refreshed baseline (+ optional raw files)
back to the repos from a netlify build. Stdlib only.

- monitor/baseline.json -> wesisad5/aizhuiguang-site-monitor @ main
  (contents API PUT), then fast-forward lane/monitor to the same commit
  (Git Data ref PATCH — the lane is what netlify builds).
- any files in refresh-capture/ -> wesisad5/aizhuiguang-scrape @ main
  (one tree+commit; e.g. llms.txt fetched raw/direct).
"""
import base64
import json
import os
import sys
import urllib.request

GH = 'https://api.github.com'
PAT = os.environ.get('GH_PAT', '')
UA = 'aizg-refresh/1.0'


def gh(path, data=None, method=None):
    req = urllib.request.Request(GH + path, data=json.dumps(data).encode() if data else None,
                                 method=method, headers={'Authorization': f'token {PAT}',
                                                         'User-Agent': UA,
                                                         'Accept': 'application/vnd.github+json'})
    if data:
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.load(r) if r.status != 204 else {}


def main():
    if not PAT:
        print('GH_PAT missing')
        return 1
    # 1. baseline -> monitor repo main
    b64 = base64.b64encode(open('monitor/baseline.json', 'rb').read()).decode()
    s, cur = gh('/repos/wesisad5/aizhuiguang-site-monitor/contents/monitor/baseline.json')
    body = {'message': 'baseline refresh (netlify direct scan)',
            'content': b64}
    if s == 200:
        body['sha'] = cur['sha']
    s, put = gh('/repos/wesisad5/aizhuiguang-site-monitor/contents/monitor/baseline.json',
                body, method='PUT')
    print('baseline put:', s)
    if s not in (200, 201):
        return 1
    main_sha = put['commit']['sha']

    # 2. fast-forward lane/monitor to main
    s, _ = gh('/repos/wesisad5/aizhuiguang-site-monitor/git/refs/heads/lane/monitor',
              {'sha': main_sha}, method='PATCH')
    print('lane fast-forward:', s)

    # 3. captured raw files -> scrape repo (one commit)
    import glob
    captures = []
    for f in sorted(glob.glob('refresh-capture/*')):
        rel = f.split('/', 1)[1]
        b64c = base64.b64encode(open(f, 'rb').read()).decode()
        s, blob = gh('/repos/wesisad5/aizhuiguang-scrape/git/blobs',
                     {'content': b64c, 'encoding': 'base64'}, method='POST')
        if s != 201:
            print('blob fail', f, s)
            return 1
        dest = 'sources/' + ('llms.txt' if 'llms' in rel else rel)
        captures.append({'path': dest, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
    if captures:
        s, br = gh('/repos/wesisad5/aizhuiguang-scrape/branches/main')
        s, tree = gh('/repos/wesisad5/aizhuiguang-scrape/git/trees',
                     {'base_tree': br['commit']['commit']['tree']['sha'], 'tree': captures},
                     method='POST')
        s, commit = gh('/repos/wesisad5/aizhuiguang-scrape/git/commits',
                       {'message': 'refresh capture: raw direct-fetch files (netlify lane)',
                        'tree': tree['sha'], 'parents': [br['commit']['sha']]}, method='POST')
        s, _ = gh('/repos/wesisad5/aizhuiguang-scrape/git/refs/heads/main',
                  {'sha': commit['sha']}, method='PATCH')
        print('scrape capture commit:', s)
    return 0


if __name__ == '__main__':
    sys.exit(main())
