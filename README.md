# aizhuiguang-site-monitor

Weekly drift monitor for **aizhuiguang.tech** — alerts on any site-surface or
data-shape change (new/removed pages, script version-stamp bumps, data-file hash
changes, inline-data block changes).

Public repo on purpose: GitHub Actions is free here (the private scrape repo's
Actions startup-fail on this account's plan). No secrets, no scraped content —
only the scanner + a fingerprint baseline.

## What it watches

| Layer | Signal | Example alert |
|---|---|---|
| Site surface | sitemap URL set | `SURFACE page ADDED to sitemap: /tools/xxx.html` |
| Script stamps | `<script src>` + `?v=` per page | `SCRIPT version stamp 20260906d → 20261013a: /tools/aizg-mini.js` |
| Data files | sha256 + size | `DATA CHANGED: /tools/drama-db.js (sha …)` |
| Inline data | per-page inline `<script>` block fingerprints | `INLINE data blocks changed on /tools/branch-drama.html` |

37 watched pages (all /tools/ + data-bearing /gallery/), 30 pinned data files,
81 sitemap URLs, 112 inline blocks in the current baseline.

## When it fires

Every Monday 09:00 UTC (manual dispatch too). On drift it:
1. opens/updates the `🔔 site drift detected` issue (email if you watch this repo),
2. uploads `monitor/report.json` as an artifact,
3. fails the job (GitHub emails the failure to the repo owner).

## After a drift alert

1. Pull the report from the run artifact (or the issue body).
2. Sync the changed files/pages into the private `aizhuiguang-scrape` repo
   (`sources/js/`, `sources/html/`, re-run the parsed-layer regeneration for the
   affected engines; translate-proxy fetch recipe in its monitor notes).
3. Refresh the baseline HERE and commit:
   ```bash
   python3 monitor/site_monitor.py --update
   git commit -am "baseline refresh <date>" && git push
   ```

## Local run

```bash
python3 monitor/site_monitor.py            # diff vs baseline; exit 1 on drift
python3 monitor/site_monitor.py --update   # re-baseline from live
```

Stdlib only. Direct HTTPS first, translate.goog proxy fallback (the site resolves
IPv6-only and hangs unknown IPv4 egress — sandboxes need the proxy; GitHub
runners go direct).
