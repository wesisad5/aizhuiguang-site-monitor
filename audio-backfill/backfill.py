#!/usr/bin/env python3
"""aizhuiguang audio backfill runner (S12) — netlify build compute.

Downloads the missing audio binaries from aizhuiguang.tech into the private
scrape repo (wesisad5/aizhuiguang-scrape) via git fast-import, in batches.

Self-chaining design (with plugins/chain-trigger in the netlify stub branch):
  - build.command runs this script
  - state lives in GIT (the repo tree IS the todo list — idempotent, self-healing)
  - this script downloads for TIME_BUDGET_S, pushes whatever it got, writes
    /tmp/backfill-summary.json, and the onPostBuild plugin triggers the next
    build unless summary.completed is true (or kill switch in Blobs).

Stdlib only. Concurrency: 4 workers. URL candidates per file (probe order).
"""
import concurrent.futures as cf
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, 'files.json')
STAGING = '/tmp/audio-staging'
REPO_DIR = '/tmp/repo-push'
GH_API = 'https://api.github.com'
GH_PAT = os.environ.get('GH_PAT', '')
SUMMARY_PATH = '/tmp/backfill-summary.json'
LOG_PATH = '/tmp/backfill-log.jsonl'

TIME_BUDGET_S = int(os.environ.get('TIME_BUDGET_S', '480'))   # 8 min downloads
MAX_FILES = int(os.environ.get('MAX_FILES', '80'))
WORKERS = int(os.environ.get('WORKERS', '4'))
UA = 'aizg-backfill/1.0 (+netlify build compute)'

MP3_MAGIC = (b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2', b'\xff\xe2',
             b'OggS', b'RIFF', b'ftyp')
MIN_BYTES = 4096
MAX_BYTES = 150 * 1024 * 1024


def log(msg):
    print(f'[backfill] {msg}', flush=True)


def gh_get(path):
    req = urllib.request.Request(GH_API + path, headers={
        'Authorization': f'token {GH_PAT}', 'User-Agent': UA, 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def have_set():
    """Current audio/ files in the scrape repo main tree (git IS the state)."""
    t = gh_get('/repos/wesisad5/aizhuiguang-scrape/git/trees/main?recursive=1')
    if t.get('truncated'):
        raise RuntimeError('tree truncated — need pagination')
    out = set()
    for e in t['tree']:
        p = e['path']
        if p.startswith('audio/') and e['type'] == 'blob':
            out.add(p[len('audio/'):])
    return out


def fetch_one(entry, deadline):
    """Download one file trying candidate URLs. Returns dict result."""
    rel = entry['file']
    dest = os.path.join(STAGING, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for url in entry['urls']:
        for attempt in range(3):
            if time.time() > deadline:
                return {'file': rel, 'ok': False, 'err': 'deadline', 'url': url}
            try:
                req = urllib.request.Request(url, headers={'User-Agent': UA})
                with urllib.request.urlopen(req, timeout=90) as r:
                    ctype = r.headers.get('Content-Type', '')
                    data = r.read()
                if len(data) < MIN_BYTES or len(data) > MAX_BYTES:
                    raise ValueError(f'size {len(data)}')
                if data[:3] not in MP3_MAGIC and data[:4] not in MP3_MAGIC:
                    raise ValueError(f'not audio: {data[:8]!r} ctype={ctype}')
                with open(dest, 'wb') as f:
                    f.write(data)
                return {'file': rel, 'ok': True, 'bytes': len(data), 'url': url,
                        'sha_pre': hashlib.sha256(data).hexdigest()[:16]}
            except Exception as e:
                if attempt == 2:
                    log(f'  FAIL {rel} via {url}: {e}')
                time.sleep(2 * (attempt + 1))
    return {'file': rel, 'ok': False, 'err': 'all-candidates-failed'}


def build_stream(path, good, head):
    """Build a git fast-import stream: one commit on top of `head` adding files."""
    when = int(time.time())
    msg = f'audio backfill batch: {len(good)} files (S12 netlify chain)\n'
    with open(path, 'wb') as f:
        f.write(b'commit refs/heads/main\n')
        f.write(f'committer aizg-backfill <bot@aizhuiguang.local> {when} +0000\n'.encode())
        f.write(f'data {len(msg)}\n'.encode())
        f.write(msg.encode())
        f.write(f'from {head}\n'.encode())
        for r in good:
            blob = open(os.path.join(STAGING, r['file']), 'rb').read()
            rel = r['file']
            f.write(f'M 100644 inline audio/{rel}\n'.encode())
            f.write(f'data {len(blob)}\n'.encode())
            f.write(blob)
            f.write(b'\n')
        f.write(b'\ndone\n')
    return len(good)


def push_batch(results):
    """Push successfully downloaded files to the scrape repo via fast-import."""
    good = [r for r in results if r['ok']]
    if not good:
        log('nothing to push')
        return 0
    # clean clone (trees+commits only, no blobs) — push adds only new blobs
    if os.path.exists(REPO_DIR):
        subprocess.run(['rm', '-rf', REPO_DIR], check=True)
    clone_url = f'https://x-access-token:{GH_PAT}@github.com/wesisad5/aizhuiguang-scrape.git'
    subprocess.run(['git', 'clone', '--filter=blob:none', '--no-checkout', clone_url, REPO_DIR],
                   check=True, capture_output=True, timeout=300)
    pushed = 0
    for attempt in range(3):
        head = subprocess.run(['git', '-C', REPO_DIR, 'rev-parse', 'refs/remotes/origin/main'],
                              capture_output=True, text=True, check=True).stdout.strip()
        stream_path = '/tmp/fi.stream'
        n = build_stream(stream_path, good, head)
        subprocess.run(['git', '-C', REPO_DIR, 'fast-import', '--quiet', '--done'],
                       stdin=open(stream_path, 'rb'), check=True,
                       timeout=600, capture_output=True)
        try:
            subprocess.run(['git', '-C', REPO_DIR, 'push', 'origin', 'main'],
                           check=True, capture_output=True, timeout=600)
            pushed = n
            break
        except subprocess.CalledProcessError:
            log(f'push attempt {attempt+1} failed — refetch and rebuild')
            subprocess.run(['git', '-C', REPO_DIR, 'fetch', 'origin', 'main',
                            '--filter=blob:none'], check=True, capture_output=True, timeout=300)
    return pushed


def main():
    t0 = time.time()
    os.makedirs(STAGING, exist_ok=True)
    manifest = json.load(open(MANIFEST))
    log(f'manifest: bgm={len(manifest["bgm"])} sfx={len(manifest["sfx"])} lib={len(manifest["lib"])}')

    have = have_set()
    log(f'have (repo tree): {len(have)} audio files')

    missing = []
    for sec in ('bgm', 'sfx', 'lib'):
        for e in manifest[sec]:
            if e['file'] not in have:
                missing.append(e)
    log(f'missing: {len(missing)}')
    if not missing:
        summary = {'completed': True, 'downloaded': 0, 'pushed': 0, 'missing_before': 0,
                   'elapsed_s': round(time.time() - t0, 1)}
        json.dump(summary, open(SUMMARY_PATH, 'w'), indent=1)
        log('ALREADY COMPLETE — no trigger')
        return 0

    work = missing[:MAX_FILES]
    deadline = time.time() + TIME_BUDGET_S
    results = []
    logf = open(LOG_PATH, 'a')
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_one, e, deadline): e for e in work}
        for fut in cf.as_completed(futs):
            r = fut.result()
            results.append(r)
            logf.write(json.dumps(r, ensure_ascii=False) + '\n')
            logf.flush()
            if r['ok']:
                log(f'  ok {r["file"]} ({r["bytes"]}B)')
            else:
                log(f'  MISS {r["file"]}: {r.get("err")}')

    ok = [r for r in results if r['ok']]
    log(f'downloaded {len(ok)}/{len(work)} in {round(time.time()-t0)}s')

    pushed = push_batch(results)
    log(f'pushed {pushed} files to scrape repo')

    have2 = have_set() if pushed else have
    remaining = sum(1 for sec in ('bgm', 'sfx', 'lib') for e in manifest[sec] if e['file'] not in have2)
    failed = [r['file'] for r in results if not r['ok'] and r.get('err') != 'deadline']
    completed = remaining == 0
    summary = {
        'completed': completed,
        'missing_before': len(missing),
        'downloaded': len(ok),
        'pushed': pushed,
        'remaining': remaining,
        'failures': failed[:20],
        'n_failures': len(failed),
        'elapsed_s': round(time.time() - t0, 1),
        'build_id': os.environ.get('DEPLOY_ID', ''),
    }
    json.dump(summary, open(SUMMARY_PATH, 'w'), indent=1)
    log(f'summary: {json.dumps(summary)[:400]}')
    log('DONE (plugin decides chaining)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
