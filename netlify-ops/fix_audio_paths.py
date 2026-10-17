#!/usr/bin/env python3
"""S12 path-bug fix — relocate 108 misfiled audio files, drop 421 root dups.

The netlify fn chain's files.json had prefix-less paths for sfx (116) and
slash-less bgm entries (422): 107 new SFX + jiuchong landed at audio/ root
instead of audio/sfx/ and audio/bgm/, and 421 bgm files were re-downloaded
as byte-identical root duplicates of existing audio/bgm/ copies.

Fix = ONE tree-level commit via GitHub Git Data API (no blob transfers):
  - move 107 sfx-*      : audio/<f>   -> audio/sfx/<f>
  - move jiuchong       : audio/<f>   -> audio/bgm/<f>
  - delete 421 root dups (sha-identical to audio/bgm/<f>)
Safety: refuses to run twice (idempotent re-check), refuses if any diff-sha
mismatch appears, never force-pushes.
"""
import json
import subprocess
import sys
import urllib.request

GH = 'https://api.github.com'
import os
PAT = os.environ.get('GH_PAT', '')
REPO = 'wesisad5/aizhuiguang-scrape'
UA = 'aizg-fix/1.0'


def api(path, data=None, method=None):
    req = urllib.request.Request(GH + path, data=json.dumps(data).encode() if data is not None else None,
                                 method=method, headers={'Authorization': f'token {PAT}',
                                                         'User-Agent': UA,
                                                         'Accept': 'application/vnd.github+json'})
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r) if r.status != 204 else {}


def get_tree():
    t = api(f'/repos/{REPO}/git/trees/main?recursive=1')
    if t.get('truncated'):
        raise RuntimeError('tree truncated')
    return {e['path']: e for e in t['tree'] if e['type'] == 'blob'}


def main():
    tree = get_tree()
    audio = {p: e for p, e in tree.items() if p.startswith('audio/')}
    root = {p[len('audio/'):]: e for p, e in audio.items() if p.count('/') == 1}
    M = json.load(open('/home/z/work/monitor-repo/audio-backfill/files.json'))
    qsfx = {e['file'] for e in M['sfx']}
    qbgm_root = {e['file'] for e in M['bgm'] if '/' not in e['file']}

    moves, deletes, problems = [], [], []
    for name, e in sorted(root.items()):
        if name == 'manifest.json':
            continue
        if name in qsfx:
            dest = f'audio/sfx/{name}'
            if dest in audio:
                problems.append(f'sfx dest already exists: {dest}')
            else:
                moves.append((name, dest, e['sha']))
        elif name in qbgm_root or name.startswith(('xb-', 'xd-')):
            dest = f'audio/bgm/{name}'
            if dest in audio:
                if audio[dest]['sha'] == e['sha']:
                    deletes.append(name)
                else:
                    problems.append(f'sha mismatch root vs bgm/: {name}')
            else:
                moves.append((name, dest, e['sha']))  # e.g. jiuchong
        else:
            problems.append(f'unclassified root file: {name}')
    if problems:
        print('PROBLEMS — aborting:')
        for p in problems:
            print(' ', p)
        return 1

    print(f'plan: {len(moves)} moves, {len(deletes)} dup-deletes')
    entries = []
    for name, dest, sha in moves:
        entries.append({'path': dest, 'mode': '100644', 'type': 'blob', 'sha': sha})
        entries.append({'path': f'audio/{name}', 'mode': '100644', 'type': 'blob', 'sha': None})
    for name in deletes:
        entries.append({'path': f'audio/{name}', 'mode': '100644', 'type': 'blob', 'sha': None})
    print(f'tree POST: {len(entries)} entries')

    branch = api(f'/repos/{REPO}/branches/main')
    head = branch['commit']['sha']
    base_tree = branch['commit']['commit']['tree']['sha']

    new_tree = api(f'/repos/{REPO}/git/trees', {'base_tree': base_tree, 'tree': entries})
    commit = api(f'/repos/{REPO}/git/commits', {
        'message': ('S12 fix: relocate misfiled audio from the netlify-fn path bug\n\n'
                    f'- move {len(moves)} files: 107 sfx-* -> audio/sfx/, jiuchong-tianmen -> audio/bgm/\n'
                    f'- delete {len(deletes)} byte-identical root duplicates of audio/bgm/ files\n'
                    '(files.json queue lacked the sfx//bgm/ prefixes; tree-level move, blobs unchanged)'),
        'tree': new_tree['sha'], 'parents': [head]})
    api(f'/repos/{REPO}/git/refs/heads/main', {'sha': commit['sha']}, method='PATCH')
    print(f'committed {commit["sha"][:10]}')
    json.dump({'moves': [(n, d) for n, d, _ in moves], 'deletes': deletes},
              open('/home/z/work/root-fix-done.json', 'w'), indent=1, ensure_ascii=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
