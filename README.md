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

37 watched pages (all /tools/ + data-bearing /gallery/ + the S14 additions: /tools/engine.html,
/tools/engine-api.html, /dianying/), 5 pinned data files, 96 sitemap URLs in the current
baseline (2026-10-27T23:18 refresh, 0 errors).

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

## ✅ LIVE — netlify weekly lane (since 2026-10-17) + lane 2 (since 2026-10-27)

The alert pipeline runs on **netlify build compute** (no GHA / no GitLab CI
needed — both were account-blocked; see netlify-ops/README.md for the full
recipes). First real fire: 2026-10-17, issue #1 (lightcut AI-agent tooling
drift) — detected, synced, and closed same-day. Second fire: 2026-10-27, issue
#2 (16 new pages + lightcut suite + local-engine tooling) — synced + closed
same-day, AND exposed that the weekly cron had been dead since Oct 18 (see
netlify-ops/README.md — fn wiped by a git build; now self-healing).

| Piece | Where |
|---|---|
| Weekly trigger (S14 final) | **cron hub**: scheduled function `weekly` (cron `0 9 * * 1`, Mon 09:00 UTC) on **aizg-vault.netlify.app** — a NO-BUILD site (raw-API deploys only), so no git build can ever replace its fn set (that is what killed the cron twice). The hub fn POSTs a `lane/monitor` build on BOTH lane sites (NETLIFY_SITE_IDS env) |
| Lane 1 | **aizg-drift-monitor.netlify.app** (wesisad5's netlify team — 300 legacy build-min/mo) — pure scan lane, no functions |
| Lane 2 (REDUNDANT) | **aizg-drift-watch.netlify.app** (second fleet account, ujaraksuzanne-coder) — same repo, same scan; either lane failing still leaves the other alerting. Proven 2026-10-27 (scanned + drift-commented issue #2) |
| Scan build | git-connected to the PUBLIC GitLab mirror of this repo (**wesisad5/aizhuiguang-site-monitor**, project 88179781 — migrated off the revoked old account 2026-10-27), branch `lane/monitor` (anonymous HTTPS clone — GitHub connections need deploy keys and fail) |
| Fn maintenance | `netlify-ops/ensure_weekly_fn.py` (raw API + the committed hub zip in netlify-ops/weekly-fn/) — run against the HUB site if its fn ever needs repair; never host fns on the build lanes |
| Drift alert | `monitor/file_drift_issue.py` — opens/comments the 🔔 issue (emails the repo owner) + the build fails (netlify deploy notification). Clean scans = green heartbeat builds |
| Manual run | `POST /sites/9fe5dd05-7ad0-4a1e-8ab0-016aabaf4b8b/builds {"branch":"lane/monitor"}` with the netlify PAT (lane 2: site `4b69b539-a662-4ff6-b2d7-6b183db725a8`), or `python3 monitor/site_monitor.py` locally |
| Re-baseline after a sync | `REFRESH_BASELINE=1` site env (account env API) → fire a build → it re-scans direct, commits monitor/baseline.json back to main + re-points the lane, and captures raw files (llms.txt) — then DELETE the env var (a left-over value makes scheduled scans silently re-baseline instead of alerting — this exact trap was found + fixed 2026-10-27) |
| Raw capture lane | draft `fetch.mjs` fn on aizg-drift-monitor-2 (credit-dead account — drafts still serve): POST `{url}` → raw bytes through netlify egress (the sandbox path to aizhuiguang.tech, which rate-limits proxies) |

Scanner notes (2026-10-17): the site burst-rate-limits (~24-50 rapid requests →
429) — the scanner uses adaptive global pacing (1.2s doubling on 429, global
cooldowns) and a 660s scan budget so the build can never hit the 15-min netlify
cap. Pinned files now include `/assets/aizg-agent.js` and `/llms.txt`.

The old GHA workflow (.github/workflows/site-monitor.yml) and GitLab CI
(.gitlab-ci.yml) remain committed as inert fallbacks if their account blocks
are ever lifted.
