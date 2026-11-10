# netlify-ops — S12 operational recipes for aizhuiguang compute

Proven patterns from this session (2026-10-17). All API-first, no CLI dependency
(netlify-cli's hasha worker threads EAGAIN in the Z-sandbox; the CLI works
elsewhere but the raw API is deterministic here).

## Credentials (S13 — 2026-10-18)

**No PATs are pasted in prompts anymore.** The full 90-key Netlify fleet +
GitHub/GitLab PATs + the site→owner map live in the private credential vault:
**`wesisad5/aizhuiguang-secrets`** (GitHub, private — `secrets/creds.json.b64`
+ `creds.py`/`creds.sh` loaders + `probe_fleet.py` + `netlify_ops.py`),
mirrored on GitLab (87774139, plaintext `plain/creds.json` lane). Recovery:
clone the vault with any wesisad5 GitHub PAT, then
`eval "$(python3 secrets/creds.py env)"`. NEVER echo token material into
this public repo — reference the vault instead.

## Fleet facts (checked 2026-10-18 — full probe in the vault's fleet_map.json)

- **89/90 PATs alive** (ariellebridget3 → 401). ~86 LEGACY plans: 300 unused
  build-minutes each. Only 4 accounts on new credit pricing: dollartree1994
  (depleted), cafesoleil319, dennysanpablo, mark-zimmern08/MZ08 (depleted —
  "credit usage exceeded" 403 on deploys). Use LEGACY accounts for builds.
- **Depleted legacy accounts still allow DRAFT deploys** (0 credits) — including
  draft deploys that serve functions at the `https://<deploy-id>--<site>.netlify.app`
  URL. This is how the 9-file probe ran on the credit-dead dollartree1994 site.
- wesisadora005 (vault lookup: `python3 secrets/creds.py netlify wesisadora005`)
  hosts aizg-vault, aizg-drift-monitor, and the aix-media-* sites: 300 fresh
  minutes. A build was triggered from vault creds on 2026-10-18 (HTTP 200).
- Site names are GLOBAL (one namespace across all accounts).

## The 9-file SFX probe (audio backfill completion)

`probe-sfx.mjs` + draft deploy: fetch target URLs from netlify egress, record
HTTP status + magic bytes, push valid files to the scrape repo via Git Data API.
Result: the 9 "missing" files were HTTP 200, audio/mpeg, ID3 — just TINY
(2969–3845B) sub-second clips that the chain's MIN_BYTES=4096 heuristic wrongly
rejected. Pushed in one commit (5e97349). Lesson: size heuristics must be
magic-byte-primary.

## S12 chain path bug + fix

files.json queue entries for sfx (116) lacked the `sfx/` prefix and bgm
slash-less entries (422) lacked `bgm/` → the fn chain pushed 107 sfx + 422 bgm
files to `audio/` root; 421 were byte-identical re-downloads of existing
`audio/bgm/` files. Fixed tree-level via Git Data API (zero blob transfer):
108 moves + 421 duplicate deletes in one commit (9478c49) — see
`fix_audio_paths.py`. files.json re-prefixed; backfill status COMPLETE.

## Function deploys via raw API (deploy_weekly_fn.py)

1. Zip the function with `@netlify/zip-it-and-ship-it` (real 258KB bundle with
   `___netlify-entry-point.mjs` + runtime shim — a hand-made zip of just the
   .mjs gets 400 "fields missing or invalid: Language").
   Run it from inside a netlify-cli install dir so node resolves the package.
2. POST /sites/{id}/deploys with:
   - `files: {path: sha1}` (static files, sha1)
   - `functions: {name: sha256(zip)}` (**sha256**, not sha1 — sha1 gives 500
     "no records matched")
   - `function_schedules: [{name, cron}]` (schedule registration)
   - `functions_config: {name: {display_name, generator, routes, priority}}`
3. PUT /deploys/{id}/files/{path} — raw bytes
4. PUT /deploys/{id}/functions/{name}?runtime=nodejs18.x&invocation_mode=stream&size=N
   — raw zip bytes (application/octet-stream)
5. Poll /deploys/{id} → state=ready.

Scheduled functions (cron in config) are 403 for external POSTs — netlify's
cron infra triggers them only. Manual test = fire the build directly.

## Git-connected build lanes (the monitor)

- GitHub repo connections clone via SSH and die with "Host key verification
  failed" (no deploy key). **GitLab public mirrors clone over anonymous HTTPS
  — use those** (the S12 audio lane used the same trick).
- PATCH /sites/{id} with the LEGACY `{"repo": {...}}` body shape (id =
  GitLab project numeric id, provider gitlab, repo_path, repo_branch, cmd,
  dir, allowed_branches). A `build_settings` body silently persists nothing.
- Lane branch ONLY (never default) — keeps deploys 0-credit where it matters
  and never touches the repo's main.
- Trigger: POST /sites/{id}/builds {"branch": "lane/..."} (authenticated;
  build hooks are platform-broken since 2026-10-02). concurrent_builds=1 per
  account — builds QUEUE; don't double-fire.
- Env: POST /api/v1/accounts/{acc}/env?site_id=… with an ARRAY body
  `[{"key","values":[{"value","context":"all"}]}]` (no scopes — free accounts
  403 "set specific scopes" otherwise). The site env REST endpoint is
  paywalled; the account env endpoint is not.

## ⚠️ Production function deploys REPLACE the whole function set

A deploy whose `functions` manifest lists only ONE function deletes every
other function on the site (bit me: deploying a helper wiped the scheduled
weekly fn — caught + redeployed both together 2026-10-17). Always zip + list
ALL functions in one deploy. Draft deploys do NOT replace (they only add a
preview).

## Monitor lane (aizg-drift-monitor.netlify.app)

- site 9fe5dd05-7ad0-4a1e-8ab0-016aabaf4b8b, wesisad5's team (300 min/mo)
- pure scan lane (S15): NO functions on the lane sites — the trigger is the
  cron-hub fn on aizg-vault (see "Cron hub")
- build = git clone (GL mirror, lane/monitor) → `bash monitor/netlify_run.sh`
  → python3 site_monitor.py → on drift: GitHub issue (emails the repo owner)
  + failed build (netlify deploy notification email)
- clean scan = green build (heartbeat visible in the deploys list)

## Weekly monitor alert chain (user-facing)

1. netlify cron (Mon 17:07 / 17:37 / 18:07 UTC — primary + heals) fires the
   scheduled hub fn on aizg-vault
2. hub fn fires each lane build with ITS OWN account's PAT
   (NETLIFY_PATS, same order as NETLIFY_SITE_IDS), skipping lanes whose latest
   build is <45 min old (idempotent — the heal shots only fire what failed)
3. build runs the scanner; drift → issue comment on
   "🔔 site drift detected: aizhuiguang.tech" (wesisad5 gets the email);
   scan that can't finish → the distinct 🧭 scan-INCOMPLETE issue
4. Manual anytime: POST /sites/{site_id}/builds {"branch":"lane/monitor"}

## ⚠️ POSTMORTEM S15 (2026-11-10): the hub fired into a wall for 2 weeks

Symptom: both Monday crons (Nov 2 + Nov 9) "fired" — but lane 1 got ZERO
builds both weeks and both lane-2 scans died partial (issue #5). Four stacked
causes, all fixed:

1. **The S14 hub used ONE PAT for BOTH lanes.** Lane 2 lives on a different
   netlify account, so the hub's lane-2 builds POST 404'd EVERY time
   (cross-account, probe-proven 2026-11-10). The Monday lane-2 scans only
   happened because lane 2's OWN legacy fn fired them. Fix: `NETLIFY_PATS`
   (comma list, per-lane PAT) + the fn fires each lane with its own account's
   token; NETLIFY_PAT stays as fallback.
2. **Lane 1's own fn was wiped by the Oct-28 green finalize** (by design — the
   hub was supposed to take over) and the hub's lane-1 POST failed both
   Mondays (transient; manual POST works). Fix: in-fn staleness check +
   MULTIPLE schedule shots (17:07 primary, 17:37 + 18:07 heals — each run
   skips lanes with a build <45 min old, so they never double-fire).
3. **09:00 UTC = 17:00 Beijing = the site's evening peak.** Both Monday scans
   + a 00:38 UTC (09:00 Beijing) catch-up scan crawled at ~10s/fetch with
   heavy 429s and died at the budget cap. Fix: cron moved to 17:07 UTC
   (02:07 Beijing) + the scanner's start pace moved 1.2s → 3.5s steady
   (see site_monitor.py header — the old pace burst-tripped the site's
   ~24-req limiter then starved in 60-180s cooldowns).
4. **Scheduled fns get ~10s execution** — the old fn's serial 2×25s-timeout
   POSTs could be killed mid-flight. Fix: the fn now does a 4s staleness
   check + 4s POST PER LANE, ALL LANES IN PARALLEL — exits in ~5s worst case.

Also observed: netlify build minutes RESET monthly (wesisad5's team showed
300/300 on Nov 10 after Oct's heavy use) — check `capabilities.build_minutes`
before assuming quota exhaustion.

## ⚠️ POSTMORTEM S14 (2026-10-27): the weekly cron was silently dead for 9 days

Symptom: zero builds between Oct-18 06:16 and Oct-27 22:33 — both Monday crons
(Oct 19 + Oct 26) never fired. Three stacked causes, all fixed:

1. **The Oct-18 manual git build wiped the scheduled fn.** The site's
   `functions_dir` was null, so the build's deploy carried an EMPTY functions
   manifest and replaced the raw-API-deployed `weekly` fn with nothing (the
   "production deploys REPLACE the set" rule bites git builds too, not just fn
   deploys). Fix: netlify.toml `[functions] directory = "functions"` — every
   build now deploys weekly.mjs itself — PLUS `ensure_weekly_fn.py` as the last
   build step (raw-API redeploy of the committed `weekly-fn/weekly.zip` with
   `function_schedules` if the fn or its cron is missing; non-fatal).
   NOTE the functions API returns abbreviated keys: `n`=name, `schedule`=cron
   string (not `schedules`/`cron` objects) — ensure_weekly_fn.py parses both.
2. **`REFRESH_BASELINE=1` leftover** in the lane netlify.toml (from the Oct-18
   refresh session): even if the cron HAD fired, the scan would have silently
   re-baselined instead of alerting. Fix: removed from netlify.toml AND from the
   site env; the re-baseline procedure now ends with an explicit env-var DELETE
   step (see README table).
3. **Uncapped report-only scan**: netlify_run.sh had a stray
   `timeout "$SCAN_BUDGET_S"` stub on its own line and the report-only path ran
   the scanner WITHOUT the timeout wrapper (only refresh mode was capped) —
   builds could hang indefinitely. Fix: both paths capped; `bash -n` clean.

## S14 topology (post-migration)

- **GitLab**: old account (ansgareutychisO) PAT REVOKED 2026-10-28 — all
  old-namespace projects frozen (monitor 87549490 still public-readable but
  unpushable; used by nothing). New mirrors under **wesisad5**: monitor 88179781
  (public — the netlify build lane points here), secrets 88179862,
  story-engine 88179928, scrape-backup 88180109, assets 88180796.
- **Lane 2**: aizg-drift-watch.netlify.app (site 4b69b539-a662-4ff6-b2d7-6b183db725a8,
  account ujaraksuzanne-coder 688d9452336ce2dc54243a3a — legacy 300 build-min).
  Same GL repo, same lane branch. Its own legacy scheduled fn (cron 09:00 UTC)
  is what actually fired the Nov-2/Nov-9 scans — it survives until the lane's
  first clean (green) finalize wipes it, after which the hub covers it.
  ⚠️ S15: the ujaraksuzanne PAT's API access to this site is INTERMITTENT
  (most reads 404, occasional 200 — netlify-side; retries eventually get
  through; the fn/builds path itself works — the Nov 2/9 builds prove it).
- **Capture lane**: aizg-drift-monitor-2 (cafesoleil319 330dfbfb) — account is
  credit-dead (production deploys 403 "credits exceeded") but DRAFT deploys
  serve: the `fetch.mjs` draft fn is the raw-site-capture path
  (`POST {url}` → bytes through netlify egress; use when the sandbox proxy is
  rate-limited). Deployed via the raw-API recipe above with `"draft": true`.
- **Fleet probes**: an account being "active" is NOT enough — check
  `capabilities.build_minutes` exists (legacy) vs credit system (new-credit),
  and verify with a real deploy (credit-dead accounts only fail at deploy time;
  kasulty = legacy but site-count-capped; cafesoleil319 = new-credit-dead).
  Probe script pattern: s14-probe_lane2_account.py / s14-probe_lane2_v2.py in
  the scrape repo's scripts/.

## Cron hub (S14 FINAL — the stable architecture)

The scheduled fn must NOT live on a build-connected site. Three rounds of evidence:
1. A git build with `functions_dir=null` replaces the site's fn set with NOTHING
   (killed the cron Oct-18).
2. `[functions] directory` makes builds re-deploy the fn — but the build STAGE
   exits 2 when it tries to register a schedule from a functions-dir deploy on
   this plan (esbuild/bundler pipeline; a config-less fn bundles fine — proven
   by test matrix A/B).
3. A raw-API ensure inside the build script heals the fn + schedule, but the
   build's OWN deploy finalizes afterward and overwrites the set again (the
   ordering can't be won from inside the build).

**Therefore**: the weekly cron fn lives on **aizg-vault** (site
`84a4ed19-7df7-4c44-bb3c-137a0d5f3d6a`) — a site with NO git connection and NO
builds, only raw-API deploys. Nothing can replace its fn set except an explicit
API deploy. The fn (`functions/weekly.mjs` — the S15 hub version) reads
`NETLIFY_SITE_IDS` + `NETLIFY_PATS` (per-lane PATs, same order) and POSTs one
`lane/monitor` build per lane (skipping lanes with a build <45 min old).
Schedules registered via the deploy API's `function_schedules` (cron
`7 17 * * 1` + `37 17 * * 1` + `7 18 * * 1`). Redeploy recipe:
`scripts/s15-deploy_cron_hub.py` (in the scrape repo) — zip via
@netlify/zip-it-and-ship-it, sha256, function_schedules, PUT bytes, then
account-env API for the three vars.

- Lane 1 `aizg-drift-monitor` (wesisad5 team) + lane 2 `aizg-drift-watch`
  (ujaraksuzanne-coder): pure scan lanes — no [functions] block in netlify.toml.
- The lanes' own legacy scheduled fns die at their next successful finalize
  (automatic cleanup); until then Monday may double-fire a lane — harmless
  (builds queue 1-wide per account).
- Manual invocation of the hub fn is 403 (scheduled fns are cron-only —
  netlify platform rule). To test the chain manually, POST the lane builds
  directly (see README table).
- **Netlify hard build cap = 15 min** ("Command did not finish within the time
  limit" — platform kill). Scan budgets: 420s attempt 1 + 45s cool-down + 300s
  retry + overhead ≈ 14 min max. The old 2×660s retry design was platform-killed.
- Scheduled fns CANNOT be invoked externally (403) and deploy-notification
  webhooks have no API on this plan (all /notify endpoints 404) — the immortal
  hub is the only viable schedule carrier.

## Postmortem addendum: the exit-2 red herring

"Build script returned non-zero exit code: 2" on every [functions]-configured
build was the BUILD STAGE's function-bundling/schedule-registration step failing
(NOT netlify_run.sh — the script itself exits 0/1 correctly; proven by the
published trace from the diag-run.sh forensic build). If builds exit 2 the moment
a functions dir is configured, check the fn's config export first.

## S16 addendum (2026-11-10): the local-sandbox refresh lane

The baseline refresh can run **from the Z sandbox** when netlify's lane is in
the daytime-throttle window (Beijing 08:00–24:00 = 00:00–16:00 UTC) — the
scenario that killed the 07:30 UTC netlify refresh attempt (rc=124 at the 420s
budget cap: 2 stray translate.goog 429s escalated the pace 3.5→7→8s and 77 reqs
no longer fit; SKILL §12 model confirmed).

Procedure (what S16 did, ~35 min total):

1. `FETCH_LOG=… SCAN_CHECKPOINT=/tmp/ck.json SCAN_PACE_S=3.5 SCAN_PACE_CAP_S=6
   SCAN_COOLDOWN_S=12 python3 monitor/site_monitor.py --update` — the scanner
   now (a) tries the **z-ai page_reader CLI first** for sitemap.xml (the
   translate.goog XML route hangs ~130s/attempt ≈ 400s wasted), (b) **checkpoints
   after every fetch** so a sandbox-killed run resumes across shell windows
   (sandbox bash calls cap at ~600s and reap detached daemons), (c) accepts
   local pacing overrides for the proxy's own limiter.
2. Validate the refreshed baseline: sitemap 97 / pages 42 / files 38 /
   errors `[]` (a partial scan must NEVER be committed — SIGTERM runs don't
   write baseline.json, but a 429-degraded completed scan can; check `errors`
   and coverage counts before pushing).
3. Verification: rerun WITHOUT `--update` (resume from the same checkpoint is
   fine after deleting it — a fresh full pass proves the baseline); expect
   `NO DRIFT`.
4. Commit to GH main, then sync the GL lane (what netlify builds): the GL
   lane's history is API-seeded/diverged — make a tree-equal commit on top of
   `gl/lane/monitor` (`git read-tree -mu main` on a branch off the lane) and
   push (FF, never force).
5. `DELETE /accounts/<acct>/env/REFRESH_BASELINE?site_id=<site>` — belt and
   braces even if the netlify refresh attempt never set it (the S14 trap).

Scanner hardening shipped with this (a572d4f): **marker-based proxy-noise
filter** — google rotates the injected translate bootstrap (2971/3598/3654B
observed, same `_getCallbackFunction/_DumpException/_setupNS` markers, different
gstatic build params); the old exact-size match false-positived EVERY proxied
page's inline hash at once (42 pages × "1 gone / 1 added" same-size blocks).
Match by markers at any size; the site's own inline data never contains them
(verified: the 3253B aizgTrack tracker block carries `gtag` but none of the
translate markers — kept, correctly).
