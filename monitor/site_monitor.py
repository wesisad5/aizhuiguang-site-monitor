#!/usr/bin/env python3
"""site_monitor.py — weekly aizhuiguang.tech surface + data drift monitor.

Watches three layers:
  1. SITE SURFACE  — the sitemap URL set (new/removed pages)
  2. PAGE SCRIPTS — every script src + ?v= version stamp on the watched pages
                    (stamp change = the site's own "data changed" signal)
  3. DATA FILES   — sha256 + size of every same-origin data JS the pages load
                    (byte-level drift even without a stamp bump)

Network: direct fetch first (works from GitHub Actions runners), then the
translate.goog proxy fallback (works from sandboxes the site blocks — its DNS is
IPv6-only + it rejects unknown IPv4 egress; see story-engine SKILL §8).

Usage:
  python3 site_monitor.py               # compare vs monitor/baseline.json; exit 1 on drift
  python3 site_monitor.py --update      # rewrite the baseline from the live site
  python3 site_monitor.py --report-only # print report, always exit 0

Baseline: monitor/baseline.json (committed). On drift in CI a GitHub issue is
filed by the workflow (gh CLI) and the job FAILS (GitHub emails the repo owner).

Stdlib only — no pip deps.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import ssl
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

SITE = "https://aizhuiguang.tech"
PROXY = "https://aizhuiguang-tech.translate.goog"  # + <path>?_x_tr_sl=auto&_x_tr_tl=en
REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "monitor" / "baseline.json"

# Pages whose scripts/stamps we watch. Every /tools/ + data-bearing /gallery/ page
# from the sitemap; story pages live under /juben/<id>.html (covered by the sitemap
# layer + the story-engine repo's own resync protocol).
WATCH_PAGES = [
    "/",
    "/tools/prompt-generator.html",
    "/tools/branch-drama.html",
    "/tools/manju.html",
    "/tools/music-prompt.html",
    "/tools/dance-bgm.html",
    "/tools/voice-studio.html",
    "/tools/asset-studio.html",
    "/tools/character-turnaround.html",
    "/tools/character-studio.html",
    "/tools/jingdi.html",
    "/tools/assets.html",
    "/tools/daily.html",
    "/tools/lightcut.html",
    "/tools/longshot.html",
    "/tools/skill-weaver.html",
    "/tools/object-lab.html",
    "/tools/studio.html",
    "/tools/jimeng-studio.html",
    "/tools/demo-script.html",
    "/tools/demo-script-2.html",
    "/tools/boxing-prompt.html",
    "/tools/lightcut-subtitles.html",
    "/tools/timer.html",
    "/gallery/mv-studio.html",
    "/gallery/viral-scene.html",
    "/gallery/transitions.html",
    "/gallery/grid-story.html",
    "/gallery/pose-prompts.html",
    "/gallery/transform.html",
    "/gallery/image-prompts.html",
    "/gallery/video-prompts.html",
    "/gallery/music-prompts.html",
    "/gallery/shorts.html",
    "/gallery/narrative.html",
    "/gallery/dameng.html",
    "/gallery/lumi-bot.html",
]

# Scripts to always fetch+hash even if no page currently references them
# (core data files known to be load-bearing).
PINNED_FILES = [
    "/tools/aizg-mini.js",
    "/tools/drama-db.js",
]

SCRIPT_RE = re.compile(r'<script[^>]*\bsrc="([^"]+)"[^>]*>')
SKIP_SRC = ("googletagmanager", "pagead2", "google-analytics", "youtube", "ytimg",
            "cdn.jsdelivr", "unpkg.com", "cdnjs")


def resolve_path(page: str, src: str) -> str:
    """Resolve a script src against the page it appears on → clean site path
    (query stripped, /-normalized). '/tools/x.html' + 'aizg-mini.js' →
    '/tools/aizg-mini.js'; '../aizg-clear.js' → '/aizg-clear.js'; '/assets/x' →
    '/assets/x' (site-absolute)."""
    if src.startswith(SITE):
        path = src[len(SITE):]
    elif src.startswith("//") or src.startswith("http"):
        return ""
    elif src.startswith("/"):
        path = src  # site-absolute
    else:
        base = page.rsplit("/", 1)[0] if "/" in page[1:] else ""
        import posixpath
        path = posixpath.normpath(f"{base}/{src}") if base else posixpath.normpath(f"/{src}")
        if not path.startswith("/"):
            path = "/" + path
    return path.split("?")[0].split("#")[0]


def page_scripts(page: str, html: str) -> dict[str, str]:
    """→ {clean script path: version stamp} for same-origin scripts."""
    out: dict[str, str] = {}
    for src in SCRIPT_RE.findall(html):
        if any(s in src for s in SKIP_SRC):
            continue
        p = resolve_path(page, src)
        if not p:
            continue
        m = re.search(r"[?&]v=([^&'\"]+)", src)
        out[p] = m.group(1) if m else ""
    return out
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# One-time direct-connect probe (shared by all fetches): the site's DNS is IPv6-only
# and it HANGS unknown IPv4 egress — a per-fetch direct attempt would burn the full
# timeout every time. Probe once, then route all fetches accordingly.
_DIRECT_OK = None


def probe_direct() -> bool:
    global _DIRECT_OK
    if _DIRECT_OK is None:
        try:
            req = urllib.request.Request(SITE + "/", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=8, context=CTX) as r:
                _DIRECT_OK = r.status == 200
        except Exception:
            _DIRECT_OK = False
    return _DIRECT_OK


FETCH_LOG = os.environ.get("FETCH_LOG", "")


def _log_fetch(url, ms, st, phase=""):
    if not FETCH_LOG:
        return
    try:
        with open(FETCH_LOG, "a") as _f:
            import json as _j
            _f.write(_j.dumps({"url": url, "ms": int(ms), "status": st, "phase": phase}) + "\n")
    except Exception:
        pass


# Global request pacing + bounded 429 handling.
# The site rate-limits (~24 rapid requests -> 429) and the original
# 15/30/45s-per-attempt backoff spiralled into 90-180s per throttled URL,
# blowing the netlify 15-min build cap (found 2026-10-17, diag5: gaps
# [0.7]*24 then 15.1/30.3/45.2 on the first 429).
_MIN_INTERVAL_S = 1.2
_LAST_REQ = [0.0]


def _pace():
    wait = _LAST_REQ[0] + _MIN_INTERVAL_S - time.time()
    if wait > 0:
        time.sleep(wait)
    _LAST_REQ[0] = time.time()


def fetch(url: str, timeout: float = 45.0, retries: int = 3) -> tuple[int, bytes]:
    """Direct (when the probe says it works — e.g. GitHub runners), proxy fallback.
    Returns (status, body). A real HTTP error status is returned as-is (no retry),
    EXCEPT 429 (translate-proxy rate limit) which retries with backoff."""
    attempts = [url] if probe_direct() else []
    attempts.append(_proxy_url(url))
    rate_hits = 0
    for attempt in range(retries):
        for target in attempts:
            _pace()
            _t0 = time.time()
            try:
                req = urllib.request.Request(target, headers={"User-Agent": UA, "Accept": "*/*"})
                with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                    body = r.read()
                    _log_fetch(target, (time.time() - _t0) * 1000, r.status)
                    return r.status, body
            except urllib.error.HTTPError as e:
                _log_fetch(target, (time.time() - _t0) * 1000, e.code, "http-error")
                if e.code == 429:
                    rate_hits += 1
                    if rate_hits >= 3:
                        return e.code, b""  # bounded: give up on this URL
                    time.sleep(20)  # one pause, then continue the attempt loop
                    break
                return e.code, b""
            except Exception as _e:
                _log_fetch(target, (time.time() - _t0) * 1000, str(_e)[:40], "except")
                continue
    return 0, b""


INLINE_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)

# Injected by the translate.goog proxy on EVERY proxied page (translate widget
# bootstrap: _DumpException/_getCallbackFunction/_loadCss). NOT site content —
# would false-positive every page's INLINE drift when scanning via proxy.
NOISE_SHAS = {"6ffaf44247d6"}  # filled precisely at module init below


def _noise_fingerprint() -> str:
    import hashlib as _h
    # recompute lazily from a known seed string would be fragile; instead we hardcode
    # the sha of the 2971B translate bootstrap and match by size+prefix heuristics
    return "6ffaf44247d6"


def inline_blocks(html: str, min_size: int = 500) -> list[dict]:
    """Fingerprint the page's big INLINE script blocks (data payloads like
    branch-drama's BR_ALL live inline, not as separate files). Small blocks are
    volatile (bootstrap/tracking) and skipped; the translate-proxy's injected
    2971B bootstrap is excluded by signature (it appears on every page and is
    fetch-route noise, not site content)."""
    out = []
    for m in INLINE_RE.finditer(html):
        code = m.group(1)
        if len(code) < min_size:
            continue
        sig = sha256(code.encode())
        if sig[:12] in NOISE_SHAS or (len(code) == 2971 and "_getCallbackFunction" in code):
            continue
        out.append({"size": len(code), "sha256": sig})
    out.sort(key=lambda b: (b["size"], b["sha256"]))
    return out


def _proxy_url(url: str) -> str:
    path = url[len(SITE):] or "/"
    return f"{PROXY}{path}?_x_tr_sl=auto&_x_tr_tl=en"


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def same_origin_scripts(html: str) -> list[str]:  # noqa: kept for API compat
    out = []
    for src in SCRIPT_RE.findall(html):
        if any(s in src for s in SKIP_SRC):
            continue
        if src.startswith(SITE):
            path = src[len(SITE):]
        elif src.startswith("//") or src.startswith("http"):
            continue
        else:
            path = src
        path = path.split("#")[0]
        out.append(path)
    return sorted(set(out))


def stamp_of(src_attr: str) -> str:
    m = re.search(r"[?&]v=([^&'\"]+)", src_attr)
    return m.group(1) if m else ""


def scan():
    report = {
        "scannedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": {},       # path -> {status, scripts: {path: stamp}}
        "files": {},       # path -> {sha256, size, via}
        "sitemap": [],     # url list
        "errors": [],
    }

    # 1. sitemap surface (translate proxy serves HTML only; r.jina.ai blocks some
    # IPs; direct works from GitHub runners; last resort: the z-ai page_reader CLI
    # when present — sandbox-only convenience)
    st, body = fetch(f"{SITE}/sitemap.xml", timeout=60)
    if st != 200 or not body.strip():
        try:
            req = urllib.request.Request(f"https://r.jina.ai/{SITE}/sitemap.xml",
                                          headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
                body = r.read()
                st = r.status
        except Exception:
            pass
    if (st != 200 or not body.strip()) and shutil.which("z-ai"):
        try:
            out = subprocess.run(["z-ai", "function", "-n", "page_reader",
                                  "-a", json.dumps({"url": f"{SITE}/sitemap.xml"})],
                                 capture_output=True, timeout=120).stdout.decode("utf-8", "replace")
            d = json.JSONDecoder().raw_decode(out[out.find("{"):])[0]
            txt = d.get("data", {}).get("html", "")
            if txt:
                body, st = txt.encode(), 200
        except Exception:
            pass
    if st == 200 and body.strip():
        report["sitemap"] = sorted(set(re.findall(r"https://aizhuiguang\.tech[^<\s\"']+",
                                                  body.decode("utf-8", "replace"))))
    else:
        report["errors"].append(f"sitemap fetch failed (status {st})")

    # 2. pages + their scripts (paths resolved against the page; stamps keyed clean)
    script_stamps: dict[str, str] = {}
    for page in WATCH_PAGES:
        st, body = fetch(f"{SITE}{page}", timeout=60)
        if st != 200 or not body:
            report["pages"][page] = {"status": st, "scripts": {}}
            if st == 0:
                report["errors"].append(f"page fetch failed: {page}")
            continue
        html = body.decode("utf-8", "replace")
        stamps = page_scripts(page, html)
        for p, v in stamps.items():
            script_stamps.setdefault(p, v)
        report["pages"][page] = {"status": st, "scripts": stamps,
                                 "inline": inline_blocks(html)}

    # 3. data files (referenced + pinned)
    for p in PINNED_FILES + sorted(script_stamps):
        if p in report["files"]:
            continue
        st, body = fetch(f"{SITE}{p}", timeout=120)
        if st == 200 and body:
            report["files"][p] = {"sha256": sha256(body), "size": len(body),
                                  "stamp": script_stamps.get(p, "")}
        else:
            report["files"][p] = {"sha256": "", "size": 0, "stamp": script_stamps.get(p, "")}
            report["errors"].append(f"data file fetch failed: {p} (status {st})")

    return report


def diff(old, new):
    """Produce the drift report. Returns list of human-readable findings."""
    out = []

    # surface
    old_map = set(old.get("sitemap", []))
    new_map = set(new.get("sitemap", []))
    for u in sorted(old_map - new_map):
        out.append(f"SURFACE  page REMOVED from sitemap: {u}")
    for u in sorted(new_map - old_map):
        out.append(f"SURFACE  page ADDED to sitemap:     {u}")

    # pages: status + scripts + inline blocks
    for page, pdata in sorted(new.get("pages", {}).items()):
        old_pdata = old.get("pages", {}).get(page)
        if old_pdata is None:
            out.append(f"PAGE     NEW watched page (status {pdata.get('status')}): {page}")
            continue
        if old_pdata.get("status") != pdata.get("status"):
            out.append(f"PAGE     status {old_pdata.get('status')} -> {pdata.get('status')}: {page}")
        old_scripts = old_pdata.get("scripts", {})
        for sp, stamp in pdata.get("scripts", {}).items():
            old_stamp = old_scripts.get(sp)
            if old_stamp is None:
                out.append(f"SCRIPT   NEW script on {page}: {sp} (v={stamp or 'none'})")
            elif old_stamp != stamp:
                out.append(f"SCRIPT   version stamp {old_stamp or 'none'} -> {stamp or 'none'}: {sp} (on {page})")
        for sp in sorted(set(old_scripts) - set(pdata.get("scripts", {}))):
            out.append(f"SCRIPT   script GONE from {page}: {sp}")
        # inline data blocks (branch-drama BR_ALL & friends)
        old_inline = {(b["size"], b["sha256"]) for b in old_pdata.get("inline", [])}
        new_inline = {(b["size"], b["sha256"]) for b in pdata.get("inline", [])}
        if old_inline != new_inline:
            gone = old_inline - new_inline
            added = new_inline - old_inline
            out.append(f"INLINE   data blocks changed on {page}: "
                       f"{len(gone)} gone / {len(added)} added or changed "
                       f"(sizes gone={sorted(s for s, _ in gone)}, added={sorted(s for s, _ in added)})")

    # data files
    for fp, fdata in sorted(new.get("files", {}).items()):
        old_fdata = old.get("files", {}).get(fp)
        if old_fdata is None:
            out.append(f"DATA     NEW data file: {fp} ({fdata['size']} bytes)")
            continue
        if old_fdata.get("sha256") != fdata.get("sha256"):
            ds = fdata["size"] - old_fdata.get("size", 0)
            out.append(f"DATA     CHANGED: {fp} (sha {old_fdata.get('sha256','')[:12]}… -> "
                       f"{fdata['sha256'][:12]}…, size {old_fdata.get('size',0)} -> {fdata['size']} ({ds:+d} bytes))")
    for fp in sorted(set(old.get("files", {})) - set(new.get("files", {}))):
        out.append(f"DATA     data file GONE: {fp}")

    for e in new.get("errors", []):
        out.append(f"ERROR    {e}")
    return out


def main():
    args = set(sys.argv[1:])
    update = "--update" in args
    report_only = "--report-only" in args

    new = scan()

    if not BASELINE.exists():
        update = True
    old = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}

    findings = [] if update else diff(old, new)

    if update:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(new, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        print(f"baseline UPDATED from live site: {len(new.get('pages', {}))} pages, "
              f"{len(new.get('files', {}))} data files, {len(new.get('sitemap', []))} sitemap urls")
        print(f"scanned at {new['scannedAt']}")
        report = {"status": "baseline-updated", "scannedAt": new["scannedAt"], "findings": []}
    else:
        print(f"scanned at {new['scannedAt']}")
        if findings:
            print(f"DRIFT: {len(findings)} finding(s)")
            for f in findings:
                print("  " + f)
            report = {"status": "drift", "scannedAt": new["scannedAt"], "findings": findings}
        else:
            print("NO DRIFT — site surface, script stamps and data hashes all match the baseline")
            report = {"status": "clean", "scannedAt": new["scannedAt"], "findings": []}

    out = REPO / "monitor" / "report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    if not report_only and report["status"] == "drift":
        sys.exit(1)


if __name__ == "__main__":
    main()
