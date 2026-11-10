// weekly.mjs v2 — aizg cron-hub function (S15 architecture, 2026-11-10).
//
// Lives on aizg-vault (a site with NO git connection and NO builds — build
// deploys replace the host site's function set, so the scheduled fn must live
// on a site that never builds; this host is only ever updated via raw API
// deploys).
//
// S15 root causes fixed (issue #5 era):
//  - The S14 hub used ONE netlify PAT for BOTH lanes — but lane 2 lives on a
//    DIFFERENT netlify account, so its builds POST always 404'd (cross-account
//    auth is impossible with a single PAT). Monday scans only kept running
//    because lane 2's own legacy fn fired them; lane 1 (own fn wiped by the
//    Oct-28 green finalize, hub POST failing) got NOTHING.
//  - Env is now NETLIFY_SITE_IDS + NETLIFY_PATS (comma lists, same order —
//    each lane is fired with ITS OWN account's PAT). NETLIFY_PAT remains as a
//    fallback for any lane without a per-lane PAT.
//  - Idempotency: a lane whose latest build is fresher than STALE_MIN minutes
//    is SKIPPED — so multiple schedules (primary + heals) can share this one
//    fn safely. Schedules: Mon 17:07 (primary), 17:37 + 18:07 (heals) — off
//    the :00 platform-cron wave, and 17:00 UTC = 01:00 Beijing (off-peak for
//    the Chinese site; the old 09:00 UTC slot = 17:00 Beijing evening peak
//    stormed both Nov-2 and Nov-9 scans into partials).
//  - Fast + PARALLEL: scheduled fns get ~10s execution — each lane does a
//    4s-capped staleness check and a 4s-capped build POST, all lanes in
//    parallel, so the fn exits well inside the budget. NO retry sleeps here:
//    the multiple schedule shots are the retry.
export default async (req) => {
  const SITES = (process.env.NETLIFY_SITE_IDS || '').split(',').map(s => s.trim()).filter(Boolean);
  const PATS = (process.env.NETLIFY_PATS || '').split(',').map(s => s.trim()).filter(Boolean);
  const FALLBACK = process.env.NETLIFY_PAT || '';
  const STALE_MIN = 45;
  const TMO = 4000;
  const out = { fired: new Date().toISOString(), lanes: [] };
  if (!SITES.length) {
    out.error = 'NETLIFY_SITE_IDS not set';
    return new Response(JSON.stringify(out), { status: 500 });
  }

  const j = (r) => r.json().catch(() => ({}));

  const one = async (site, pat, i) => {
    const lane = { site: site.slice(0, 8) };
    if (!pat) { lane.error = 'no PAT for lane'; return lane; }
    try {
      // staleness check: latest build fresher than STALE_MIN -> skip (idempotent)
      const c = await fetch(`https://api.netlify.com/api/v1/sites/${site}/builds?per_page=1`,
        { headers: { Authorization: `Bearer ${pat}` }, signal: AbortSignal.timeout(TMO) });
      if (c.status === 200) {
        const builds = await j(c);
        const last = Array.isArray(builds) && builds[0] ? new Date(builds[0].created_at).getTime() : 0;
        const ageMin = last ? Math.round((Date.now() - last) / 60000) : -1;
        lane.lastBuildMinAgo = ageMin;
        if (ageMin >= 0 && ageMin < STALE_MIN) { lane.action = 'fresh-skip'; return lane; }
      } else {
        lane.checkHttp = c.status; // unreadable != fresh; fall through and fire
      }
    } catch (e) { lane.checkError = String(e).slice(0, 80); }
    try {
      const r = await fetch(`https://api.netlify.com/api/v1/sites/${site}/builds`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${pat}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ branch: 'lane/monitor' }),
        signal: AbortSignal.timeout(TMO),
      });
      const b = await j(r);
      lane.http = r.status;
      lane.build = (b.id || '').slice(0, 12);
    } catch (e) {
      lane.error = String(e).slice(0, 80);
    }
    return lane;
  };

  out.lanes = await Promise.all(SITES.map((s, i) => one(s, PATS[i] || FALLBACK, i)));
  return new Response(JSON.stringify(out), { status: 200 });
};
