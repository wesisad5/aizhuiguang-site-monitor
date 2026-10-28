// weekly.mjs — aizg cron-hub function (S14 final architecture).
//
// Lives on aizg-vault (a site with NO git connection and NO builds — build
// deploys replace the host site's function set, so the scheduled fn must live
// on a site that never builds; this host is only ever updated via raw API
// deploys).
//
// Cron Mon 09:00 UTC (registered via the deploy API's function_schedules — NOT
// a config export: the build stage exits 2 trying to register schedules from
// functions-dir deploys on this plan).
//
// Duty: POST one lane build per site id in NETLIFY_SITE_IDS (the two drift
// scan lanes, on two different accounts — either build lane dying still leaves
// the other scanning). Manual trigger: POST this fn's URL.
const PAT = process.env.NETLIFY_PAT;
const SITES = (process.env.NETLIFY_SITE_IDS || '').split(',').map(s => s.trim()).filter(Boolean);

export default async (req) => {
  const out = { fired: new Date().toISOString(), lanes: SITES };
  if (!PAT || !SITES.length) {
    out.error = 'NETLIFY_PAT / NETLIFY_SITE_IDS not set';
    return new Response(JSON.stringify(out), { status: 500 });
  }
  out.results = [];
  for (const site of SITES) {
    try {
      const r = await fetch(`https://api.netlify.com/api/v1/sites/${site}/builds`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${PAT}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ branch: 'lane/monitor' }),
        signal: AbortSignal.timeout(25000),
      });
      const j = await r.json().catch(() => ({}));
      out.results.push({ site: site.slice(0, 8), http: r.status, build: (j.id || '').slice(0, 12) });
    } catch (e) {
      out.results.push({ site: site.slice(0, 8), error: String(e).slice(0, 120) });
    }
  }
  return new Response(JSON.stringify(out), { status: 200 });
};
