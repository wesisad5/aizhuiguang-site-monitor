#!/usr/bin/env python3
"""file_drift_issue.py — open/update the drift alert issue (GitHub API).

Same alert contract as the GHA workflow: one open issue titled
"site drift detected" that gets a new comment per drift event (the owner
is a participant → GitHub emails every comment). Stdlib only.
"""
import json
import os
import sys
import urllib.request

GH = 'https://api.github.com'
REPO = 'wesisad5/aizhuiguang-site-monitor'
PAT = os.environ.get('GH_PAT', '')
TITLE = '🔔 site drift detected: aizhuiguang.tech'


def gh(path, data=None, method=None):
    req = urllib.request.Request(GH + path, data=json.dumps(data).encode() if data else None,
                                 method=method, headers={
                                     'Authorization': f'token {PAT}',
                                     'User-Agent': 'aizg-monitor/1.0',
                                     'Accept': 'application/vnd.github+json'})
    if data:
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r) if r.status != 204 else {}


def main():
    if not PAT:
        print('GH_PAT not set — cannot file issue (alert degraded to build failure)')
        return 1
    report = json.load(open('monitor/report.json'))
    findings = report.get('findings', [])
    lines = [
        'The weekly site monitor detected changes on the live site.',
        '',
        f"**Scanned:** {report.get('scannedAt')}",
        '',
        '```',
        *[f'  {f}' for f in findings[:60]],
        '```',
        '',
        '---',
        '_Next steps: sync the changed data into the private scrape repo '
        '(sources/js/, sources/html/, re-run parsed-layer regeneration), then '
        'refresh the baseline: `python3 monitor/site_monitor.py --update` + commit._',
        '_Scan lane: netlify scheduled function (Mon 09:00 UTC) → build → this issue. '
        'Manual: `python3 monitor/site_monitor.py`._',
    ]
    body = '\n'.join(lines)

    issues = gh(f'/repos/{REPO}/issues?state=open&per_page=50')
    existing = next((i for i in issues if i['title'] == TITLE), None)
    if existing:
        gh(f'/repos/{REPO}/issues/{existing["number"]}/comments', {'body': body}, method='POST')
        print(f'commented on issue #{existing["number"]}')
    else:
        i = gh(f'/repos/{REPO}/issues', {'title': TITLE, 'body': body, 'labels': ['site-drift']}, method='POST')
        print(f'opened issue #{i.get("number")}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
