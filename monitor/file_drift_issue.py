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
    global TITLE
    if not PAT:
        print('GH_PAT not set — cannot file issue (alert degraded to build failure)')
        return 1
    try:
        report = json.load(open('monitor/report.json'))
    except Exception:
        report = {'status': 'partial', 'scannedAt': 'unknown', 'errors': ['report.json missing/invalid']}
    status = report.get('status', 'error')
    findings = report.get('findings', [])
    if status == 'partial':
        TITLE = '🧭 site scan INCOMPLETE: aizhuiguang.tech'
        summary = [
            'The scan could not finish within its budget (even after one retry) — the',
            'site was rate-limiting or unreachable. **This is a scan-infra alert, NOT a',
            'drift report**: coverage was partial, so absence of findings below means',
            'nothing. Partial coverage:',
            '',
            f"- pages scanned: {len(report.get('pages', {}))}",
            f"- files hashed: {len(report.get('files', {}))}",
            f"- sitemap URLs: {len(report.get('sitemap', []))}",
            f"- errors: {report.get('errors', [])}",
        ]
        nxt = ('_Next steps: re-run the scan manually (build POST or `python3 '
               'monitor/site_monitor.py`); if the site is 429-storming, wait for the '
               'storm window to pass. Do NOT re-baseline on a partial scan._')
    else:
        summary = ['The weekly site monitor detected changes on the live site.']
        nxt = ('_Next steps: sync the changed data into the private scrape repo '
               '(sources/js/, sources/html/, re-run parsed-layer regeneration), then '
               'refresh the baseline: `python3 monitor/site_monitor.py --update` + commit._')
    lines = [
        *summary,
        '',
        f"**Scanned:** {report.get('scannedAt')} (status: {status})",
        '',
        '```',
        *[f'  {f}' for f in (findings or report.get('errors', []))[:60]],
        '```',
        '',
        '---',
        nxt,
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
