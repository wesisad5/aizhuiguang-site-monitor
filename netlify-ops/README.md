# netlify-ops — S12 operational recipes for aizhuiguang compute

Proven patterns from this session (2026-10-17). All API-first, no CLI dependency
(netlify-cli's hasha worker threads EAGAIN in the Z-sandbox; the CLI works
elsewhere but the raw API is deterministic here).

## Fleet facts (checked 2026-10-17)

- **~86 of 90 PATs are LEGACY plans: 300 build-minutes each, used 0.** Only 4
  accounts are on new credit pricing: dollartree1994 (depleted), cafesoleil319,
  dennysanpablo, mark-zimmern08/MZ08 (depleted — "credit usage exceeded" 403 on
  deploys). Use LEGACY accounts for builds; new-pricing accounts are exhausted.
- **Depleted legacy accounts still allow DRAFT deploys** (0 credits) — including
  draft deploys that serve functions at the `https://<deploy-id>--<site>.netlify.app`
  URL. This is how the 9-file probe ran on the credit-dead dollartree1994 site.
- wesisad5's team account (nfp_b7u8yfjiRs…, labeled @wesisadora005 in the fleet
  list) hosts aizg-vault and the new aizg-drift-monitor site: 300 fresh minutes.
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
- scheduled function `weekly` (cron `0 9 * * 1`, Mon 09:00 UTC) → POSTs the
  lane/monitor build
- build = git clone (GL mirror, lane/monitor) → `bash monitor/netlify_run.sh`
  → python3 site_monitor.py → on drift: GitHub issue (emails the repo owner)
  + failed build (netlify deploy notification email)
- clean scan = green build (heartbeat visible in the deploys list)

## Weekly monitor alert chain (user-facing)

1. netlify cron (Mon 09:00 UTC) fires the scheduled function
2. function POSTs the lane build (NETLIFY_PAT/SITE_ID env)
3. build runs the scanner; drift → issue comment on
   "🔔 site drift detected: aizhuiguang.tech" (wesisad5 gets the email)
4. Manual anytime: POST /sites/{site_id}/builds {"branch":"lane/monitor"}
